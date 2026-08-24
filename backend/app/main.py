import json
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .models import CrewChangePerson, CrewManifest, CrewMember, Forecast, TonnageApplication, Vessel, Voyage
from .paths import FRONTEND_DIR
from .schemas import CrewChangeCreate, CrewChangePersonUpdate, TextExtractRequest, TonnageCreate, VesselCreate, VoyageCreate, VoyageUpdate
from .services.forecast import generate_forecast
from .services.importers import parse_crew_file
from .services.exporters import export_border_inspection, export_crew_change, export_crew_change_customs, export_health_declaration, export_inbound_form, export_outer_field_receipt, export_tonnage
from .services.ocr import recognize_screenshot
from .services.text_extractor import parse_fixed_text


Base.metadata.create_all(engine)
app = FastAPI(title="船代业务表单系统", version="0.1.0")


def vessel_dict(item):
    return {
        "id": item.id, "imo": item.imo, "chinese_name": item.chinese_name,
        "english_name": item.english_name, "nationality": item.nationality,
        "call_sign": item.call_sign, "shipping_company": item.shipping_company,
        "net_tonnage": item.net_tonnage, "gross_tonnage": item.gross_tonnage,
        "mmsi": item.mmsi, "extra": json.loads(item.extra_json or "{}"),
    }


def voyage_dict(item):
    fields = ("id", "vessel_id", "inbound_voyage_no", "outbound_voyage_no", "arrival_time", "departure_time", "berth", "previous_port", "previous_port_country", "previous_port_departure_time", "next_port", "next_port_country", "route", "entry_type", "crew_change", "created_at", "updated_at")
    return {field: getattr(item, field).isoformat() if hasattr(getattr(item, field), "isoformat") else getattr(item, field) for field in fields}


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ship-agency-mvp"}


@app.post("/api/text-extract/parse")
def parse_text_extract(payload: TextExtractRequest):
    result = parse_fixed_text(payload.text)
    if result["recognized_count"] == 0:
        raise HTTPException(400, "没有识别到固定格式字段，请检查粘贴内容")
    return result


@app.get("/api/vessels")
def list_vessels(db: Session = Depends(get_db)):
    return [vessel_dict(item) for item in db.scalars(select(Vessel).order_by(Vessel.id.desc())).all()]


@app.get("/api/vessels/by-imo")
def find_vessel_by_imo(imo: str, db: Session = Depends(get_db)):
    normalized_imo = imo.strip()
    if not normalized_imo:
        raise HTTPException(400, "IMO不能为空")
    item = db.scalars(
        select(Vessel).where(Vessel.imo == normalized_imo).order_by(Vessel.id.desc())
    ).first()
    if not item:
        raise HTTPException(404, "未找到该IMO对应的船舶档案")
    return vessel_dict(item)


@app.post("/api/vessels")
def create_vessel(payload: VesselCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"extra"})
    data["imo"] = (data.get("imo") or "").strip() or None
    if data["imo"] and db.scalars(select(Vessel).where(Vessel.imo == data["imo"])).first():
        raise HTTPException(409, "该IMO已存在船舶档案，请在船舶档案管理页面编辑已有记录")
    item = Vessel(**data, extra_json=json.dumps(payload.extra, ensure_ascii=False))
    db.add(item)
    db.commit()
    db.refresh(item)
    return vessel_dict(item)


@app.put("/api/vessels/{vessel_id}")
def update_vessel(vessel_id: int, payload: VesselCreate, db: Session = Depends(get_db)):
    item = db.get(Vessel, vessel_id)
    if not item:
        raise HTTPException(404, "船舶档案不存在")
    data = payload.model_dump(exclude={"extra"})
    data["imo"] = (data.get("imo") or "").strip() or None
    if data["imo"]:
        duplicate = db.scalars(
            select(Vessel).where(Vessel.imo == data["imo"], Vessel.id != vessel_id)
        ).first()
        if duplicate:
            raise HTTPException(409, "该IMO已存在另一条船舶档案，不能重复保存")
    for key, value in data.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return vessel_dict(item)


def remove_voyage_records(db: Session, voyage_id: int):
    """删除航次及其关联业务记录，供航次删除和船舶级联删除共用。"""
    manifests = db.scalars(select(CrewManifest).where(CrewManifest.voyage_id == voyage_id)).all()
    for manifest in manifests:
        crew_members = db.scalars(select(CrewMember).where(CrewMember.manifest_id == manifest.id)).all()
        for crew_member in crew_members:
            db.delete(crew_member)
        db.delete(manifest)
    for model in (CrewChangePerson, TonnageApplication, Forecast):
        related = db.scalars(select(model).where(model.voyage_id == voyage_id)).all()
        for record in related:
            db.delete(record)
    voyage = db.get(Voyage, voyage_id)
    if voyage:
        db.delete(voyage)


@app.delete("/api/vessels/{vessel_id}")
def delete_vessel(vessel_id: int, cascade: bool = False, db: Session = Depends(get_db)):
    item = db.get(Vessel, vessel_id)
    if not item:
        raise HTTPException(404, "船舶档案不存在")
    voyages = db.scalars(select(Voyage).where(Voyage.vessel_id == vessel_id)).all()
    if voyages and not cascade:
        raise HTTPException(
            409,
            f"该船舶已有{len(voyages)}条航次及关联业务数据；如确认删除，请再次确认",
        )
    for voyage in voyages:
        remove_voyage_records(db, voyage.id)
    db.delete(item)
    db.commit()
    return {"deleted": vessel_id, "deleted_voyages": len(voyages)}


def find_duplicate_voyage(db: Session, vessel_id: int, inbound: str | None, outbound: str | None, exclude_id: int | None = None):
    if not inbound and not outbound:
        return None
    statement = select(Voyage).where(
        Voyage.vessel_id == vessel_id,
        Voyage.inbound_voyage_no == inbound,
        Voyage.outbound_voyage_no == outbound,
    )
    if exclude_id is not None:
        statement = statement.where(Voyage.id != exclude_id)
    return db.scalars(statement).first()


@app.post("/api/voyages")
def create_voyage(payload: VoyageCreate, db: Session = Depends(get_db)):
    if not db.get(Vessel, payload.vessel_id):
        raise HTTPException(404, "船舶档案不存在")
    data = payload.model_dump(exclude={"extra"})
    data["inbound_voyage_no"] = (data.get("inbound_voyage_no") or "").strip() or None
    data["outbound_voyage_no"] = (data.get("outbound_voyage_no") or "").strip() or None
    if find_duplicate_voyage(db, payload.vessel_id, data["inbound_voyage_no"], data["outbound_voyage_no"]):
        raise HTTPException(409, "该船舶已经存在相同的进出港航次号，请从历史航次中继续操作")
    item = Voyage(**data, extra_json=json.dumps(payload.extra, ensure_ascii=False))
    db.add(item)
    db.commit()
    db.refresh(item)
    return voyage_dict(item)


@app.put("/api/voyages/{voyage_id}")
def update_voyage(voyage_id: int, payload: VoyageUpdate, db: Session = Depends(get_db)):
    item = db.get(Voyage, voyage_id)
    if not item:
        raise HTTPException(404, "航次不存在")
    if not db.get(Vessel, payload.vessel_id):
        raise HTTPException(404, "船舶档案不存在")
    data = payload.model_dump(exclude={"extra", "crew_change"})
    data["inbound_voyage_no"] = (data.get("inbound_voyage_no") or "").strip() or None
    data["outbound_voyage_no"] = (data.get("outbound_voyage_no") or "").strip() or None
    if find_duplicate_voyage(db, payload.vessel_id, data["inbound_voyage_no"], data["outbound_voyage_no"], voyage_id):
        raise HTTPException(409, "该船舶已经存在相同的进出港航次号，请从历史航次中继续操作")
    for key, value in data.items():
        setattr(item, key, value)
    item.extra_json = json.dumps(payload.extra, ensure_ascii=False)
    db.commit()
    db.refresh(item)
    return voyage_dict(item)


@app.get("/api/voyages/{voyage_id}")
def get_voyage(voyage_id: int, db: Session = Depends(get_db)):
    item = db.get(Voyage, voyage_id)
    if not item:
        raise HTTPException(404, "航次不存在")
    return voyage_dict(item)


@app.delete("/api/voyages/{voyage_id}")
def delete_voyage(voyage_id: int, db: Session = Depends(get_db)):
    item = db.get(Voyage, voyage_id)
    if not item:
        raise HTTPException(404, "航次不存在")
    remove_voyage_records(db, voyage_id)
    db.commit()
    return {"deleted": voyage_id}


@app.get("/api/voyages")
def list_voyages(db: Session = Depends(get_db)):
    items = db.scalars(select(Voyage).order_by(Voyage.updated_at.desc(), Voyage.id.desc())).all()
    result = []
    for item in items:
        row = voyage_dict(item)
        vessel = db.get(Vessel, item.vessel_id)
        row["vessel_chinese_name"] = vessel.chinese_name if vessel else None
        row["vessel_english_name"] = vessel.english_name if vessel else None
        row["vessel_imo"] = vessel.imo if vessel else None
        result.append(row)
    return result


@app.post("/api/voyages/{voyage_id}/touch")
def touch_voyage(voyage_id: int, db: Session = Depends(get_db)):
    item = db.get(Voyage, voyage_id)
    if not item:
        raise HTTPException(404, "航次不存在")
    from datetime import datetime
    item.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": voyage_id, "updated_at": item.updated_at.isoformat()}


@app.post("/api/voyages/{voyage_id}/crew/import")
def import_crew(voyage_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not db.get(Voyage, voyage_id):
        raise HTTPException(404, "航次不存在")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xls", ".xlsx"}:
        raise HTTPException(400, "目前只支持 .xls 或 .xlsx 船员名单")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        shutil.copyfileobj(file.file, temp)
        temp_path = temp.name
    try:
        members, meta = parse_crew_file(temp_path)
    except Exception as exc:
        raise HTTPException(400, f"船员表解析失败：{exc}") from exc
    finally:
        Path(temp_path).unlink(missing_ok=True)
    previous = db.scalars(select(CrewManifest).where(CrewManifest.voyage_id == voyage_id).order_by(CrewManifest.version.desc())).first()
    manifest = CrewManifest(voyage_id=voyage_id, source_filename=file.filename, source_type=meta["source_type"], version=(previous.version + 1 if previous else 1))
    db.add(manifest)
    db.flush()
    for row in members:
        db.add(CrewMember(manifest_id=manifest.id, name=row["name"], gender=row["gender"], nationality=row["nationality"], birth_date=row["birth_date"], document_no=row["document_no"], rank=row["rank"], extra_json=json.dumps(row["extra"], ensure_ascii=False)))
    db.commit()
    return {"manifest_id": manifest.id, "version": manifest.version, "count": len(members), "meta": meta}


@app.post("/api/voyages/{voyage_id}/source-image")
def import_source_image(voyage_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    suffix = Path(file.filename or ".png").suffix.lower() or ".png"
    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise HTTPException(400, "目前只支持 PNG/JPG 图片")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
        shutil.copyfileobj(file.file, temp)
        temp_path = temp.name
    try:
        result = recognize_screenshot(temp_path)
    except Exception as exc:
        raise HTTPException(400, f"图片识别失败，可改用人工填写：{exc}") from exc
    finally:
        Path(temp_path).unlink(missing_ok=True)
    fields = result["fields"]
    vessel = db.get(Vessel, voyage.vessel_id)
    for key in ("english_name", "chinese_name", "mmsi"):
        if key in fields:
            setattr(vessel, key, fields[key])
    for key in ("inbound_voyage_no", "outbound_voyage_no", "berth", "previous_port", "previous_port_country", "previous_port_departure_time", "next_port", "next_port_country", "route"):
        if key in fields:
            setattr(voyage, key, fields[key])
    if "entry_type" in fields:
        voyage.entry_type = "入港" if "入港" in fields["entry_type"] else "入境" if "入境" in fields["entry_type"] else voyage.entry_type
    extra = json.loads(vessel.extra_json or "{}")
    extra["last_source_image"] = file.filename
    extra["ship_system_no"] = fields.get("ship_system_no")
    extra["ocr_rows"] = result["rows"]
    vessel.extra_json = json.dumps(extra, ensure_ascii=False)
    db.commit()
    return {"fields": {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in fields.items()}, "missing_fields": result["missing_fields"], "message": "已自动带入识别到的字段，缺失字段请人工补充"}


@app.post("/api/voyages/{voyage_id}/crew-change")
def create_crew_change(voyage_id: int, payload: CrewChangeCreate, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    for person in payload.people:
        db.add(CrewChangePerson(voyage_id=voyage_id, **person.model_dump()))
    voyage.crew_change = bool(payload.people)
    db.commit()
    return {"ok": True, "count": len(payload.people)}


@app.put("/api/crew-change/{person_id}")
def update_crew_change(person_id: int, payload: CrewChangePersonUpdate, db: Session = Depends(get_db)):
    person = db.get(CrewChangePerson, person_id)
    if not person:
        raise HTTPException(404, "换班人员记录不存在")
    for key, value in payload.model_dump().items():
        setattr(person, key, value)
    db.commit()
    return {"ok": True, "id": person.id}


@app.delete("/api/crew-change/{person_id}")
def delete_crew_change(person_id: int, db: Session = Depends(get_db)):
    person = db.get(CrewChangePerson, person_id)
    if not person:
        raise HTTPException(404, "换班人员记录不存在")
    voyage_id = person.voyage_id
    db.delete(person)
    db.flush()
    if not db.scalars(select(CrewChangePerson).where(CrewChangePerson.voyage_id == voyage_id)).first():
        voyage = db.get(Voyage, voyage_id)
        if voyage:
            voyage.crew_change = False
    db.commit()
    return {"ok": True, "id": person_id}


@app.post("/api/voyages/{voyage_id}/tonnage")
def save_tonnage(voyage_id: int, payload: TonnageCreate, db: Session = Depends(get_db)):
    if not db.get(Voyage, voyage_id):
        raise HTTPException(404, "航次不存在")
    item = db.scalars(select(TonnageApplication).where(TonnageApplication.voyage_id == voyage_id)).first()
    if not item:
        item = TonnageApplication(voyage_id=voyage_id)
        db.add(item)
    item.amount = payload.amount
    item.pre_entry_no = payload.pre_entry_no
    item.duration_days = payload.duration_days
    item.purchase_date = payload.purchase_date
    item.charter_relation = payload.charter_relation
    db.commit()
    return {"ok": True, "id": item.id}


@app.get("/api/voyages/{voyage_id}/export/tonnage")
def export_tonnage_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    application = db.scalars(select(TonnageApplication).where(TonnageApplication.voyage_id == voyage_id)).first()
    if not application:
        raise HTTPException(400, "请先保存吨税申请信息")
    try:
        output = export_tonnage(db.get(Vessel, voyage.vessel_id), voyage, application)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(output, filename=output.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _voyage_crew(db: Session, voyage_id: int):
    manifest = db.scalars(
        select(CrewManifest).where(CrewManifest.voyage_id == voyage_id).order_by(CrewManifest.version.desc())
    ).first()
    if not manifest:
        return []
    return db.scalars(select(CrewMember).where(CrewMember.manifest_id == manifest.id).order_by(CrewMember.id)).all()


@app.get("/api/voyages/{voyage_id}/export/strong-general")
def export_strong_general_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    crew = _voyage_crew(db, voyage_id)
    try:
        output = export_inbound_form(vessel, voyage, crew, "general")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(output, filename=output.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/voyages/{voyage_id}/export/customs-cargo")
def export_customs_cargo_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    crew = _voyage_crew(db, voyage_id)
    try:
        output = export_inbound_form(vessel, voyage, crew, "cargo")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(output, filename=output.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/voyages/{voyage_id}/export/health-declaration")
def export_health_declaration_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    manifest = db.scalars(
        select(CrewManifest).where(CrewManifest.voyage_id == voyage_id).order_by(CrewManifest.version.desc())
    ).first()
    crew = db.scalars(select(CrewMember).where(CrewMember.manifest_id == manifest.id)).all() if manifest else []
    try:
        output = export_health_declaration(vessel, voyage, crew)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(output, filename=output.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/voyages/{voyage_id}/export/outer-field-receipt")
def export_outer_field_receipt_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    crew = _voyage_crew(db, voyage_id)
    output = export_outer_field_receipt(vessel, voyage, crew)
    return FileResponse(
        output,
        filename=output.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/api/voyages/{voyage_id}/export/border-inspection")
def export_border_inspection_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    crew = _voyage_crew(db, voyage_id)
    changes = db.scalars(
        select(CrewChangePerson).where(CrewChangePerson.voyage_id == voyage_id).order_by(CrewChangePerson.id)
    ).all()
    try:
        output = export_border_inspection(vessel, voyage, crew, changes)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return FileResponse(
        output,
        filename=output.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/voyages/{voyage_id}/export/crew-change")
def export_crew_change_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    people = db.scalars(select(CrewChangePerson).where(CrewChangePerson.voyage_id == voyage_id)).all()
    output = export_crew_change(db.get(Vessel, voyage.vessel_id), voyage, people)
    return FileResponse(output, filename=output.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.get("/api/voyages/{voyage_id}/export/crew-change-customs")
def export_crew_change_customs_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    people = db.scalars(select(CrewChangePerson).where(CrewChangePerson.voyage_id == voyage_id)).all()
    output = export_crew_change_customs(db.get(Vessel, voyage.vessel_id), voyage, people)
    return FileResponse(output, filename=output.name, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@app.get("/api/voyages/{voyage_id}/summary")
def summary(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    manifest = db.scalars(select(CrewManifest).where(CrewManifest.voyage_id == voyage_id).order_by(CrewManifest.version.desc())).first()
    crew = db.scalars(select(CrewMember).where(CrewMember.manifest_id == manifest.id)).all() if manifest else []
    changes = db.scalars(select(CrewChangePerson).where(CrewChangePerson.voyage_id == voyage_id)).all()
    tonnage = db.scalars(select(TonnageApplication).where(TonnageApplication.voyage_id == voyage_id)).first()
    latest = db.scalars(select(Forecast).where(Forecast.voyage_id == voyage_id).order_by(Forecast.version.desc())).first()
    from collections import Counter
    crew_rows = [{"id": x.id, "name": x.name, "gender": x.gender, "nationality": x.nationality, "birth_date": x.birth_date.isoformat() if x.birth_date else None, "document_no": x.document_no, "rank": x.rank} for x in crew]
    change_rows = [{"id": x.id, "direction": x.direction, "name": x.name, "nationality": x.nationality, "gender": x.gender, "birth_date": x.birth_date.isoformat() if x.birth_date else None, "document_no": x.document_no, "rank": x.rank, "reason": x.reason, "temporary_entry_permit": x.temporary_entry_permit, "flight_no": x.flight_no, "flight_time": x.flight_time.isoformat() if x.flight_time else None, "route": x.route} for x in changes]
    return {
        "voyage": voyage_dict(voyage), "vessel": vessel_dict(vessel), "crew_count": len(crew),
        "captain": next((x.name for x in crew if (x.rank or "").lower() in {"船长", "master", "1-船长"}), None),
        "nationality_stats": dict(Counter(x.nationality or "待人工填写" for x in crew)),
        "gender_stats": dict(Counter(x.gender or "待人工填写" for x in crew)), "crew": crew_rows, "crew_change": change_rows,
        "tonnage": {"amount": tonnage.amount, "pre_entry_no": tonnage.pre_entry_no, "duration_days": tonnage.duration_days, "purchase_date": tonnage.purchase_date.isoformat() if tonnage.purchase_date else None} if tonnage else None,
        "latest_forecast": {"content": latest.content, "missing_fields": json.loads(latest.missing_fields_json), "version": latest.version} if latest else None,
    }


@app.post("/api/voyages/{voyage_id}/forecast")
def create_forecast(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    manifest = db.scalars(select(CrewManifest).where(CrewManifest.voyage_id == voyage_id).order_by(CrewManifest.version.desc())).first()
    crew = db.scalars(select(CrewMember).where(CrewMember.manifest_id == manifest.id)).all() if manifest else []
    changes = db.scalars(
        select(CrewChangePerson).where(CrewChangePerson.voyage_id == voyage_id).order_by(CrewChangePerson.id)
    ).all()
    content, missing = generate_forecast(vessel, voyage, crew, changes)
    previous = db.scalars(select(Forecast).where(Forecast.voyage_id == voyage_id).order_by(Forecast.version.desc())).first()
    item = Forecast(voyage_id=voyage_id, version=(previous.version + 1 if previous else 1), content=content, missing_fields_json=json.dumps(missing, ensure_ascii=False))
    db.add(item)
    db.commit()
    return {"content": content, "missing_fields": missing, "version": item.version}


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/vessels")
def vessels_page():
    return FileResponse(FRONTEND_DIR / "vessels.html")


@app.get("/voyages")
def voyages_page():
    return FileResponse(FRONTEND_DIR / "voyages.html")
