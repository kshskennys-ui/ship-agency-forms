import json
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from .db import Base, engine, get_db
from .models import CrewChangePerson, CrewManifest, CrewMember, ExitStampApplicant, Forecast, TemporaryEntryApplicant, TonnageApplication, Vessel, Voyage
from .paths import FRONTEND_DIR
from .schemas import CrewChangeCreate, CrewChangePersonUpdate, ExitStampApplicantCreate, TextExtractRequest, TemporaryEntryApplicantCreate, TonnageCreate, VesselCreate, VoyageCreate, VoyageUpdate
from .services.forecast import berth_text, generate_forecast, normalize_port
from .services.importers import parse_crew_file
from .services.exporters import export_border_inspection, export_crew_change, export_crew_change_customs, export_exit_stamp_application, export_health_declaration, export_inbound_form, export_maritime_preapproval, export_outer_field_receipt, export_temporary_entry, export_tonnage
from .services.text_extractor import parse_fixed_text


Base.metadata.create_all(engine)


def _ensure_schema():
    """Apply the small additive migrations needed by the SQLite MVP database."""
    columns = {column["name"] for column in inspect(engine).get_columns("voyages")}
    if "customs_inspection" not in columns:
        with engine.begin() as connection:
            default_value = "FALSE" if engine.dialect.name == "postgresql" else "0"
            connection.execute(text(f"ALTER TABLE voyages ADD COLUMN customs_inspection BOOLEAN NOT NULL DEFAULT {default_value}"))


_ensure_schema()
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
    fields = ("id", "vessel_id", "inbound_voyage_no", "outbound_voyage_no", "arrival_time", "departure_time", "berth", "previous_port", "previous_port_country", "previous_port_departure_time", "next_port", "next_port_country", "route", "entry_type", "crew_change", "customs_inspection", "created_at", "updated_at")
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
    for model in (CrewChangePerson, TemporaryEntryApplicant, ExitStampApplicant, TonnageApplication, Forecast):
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
    if item.vessel_id != payload.vessel_id:
        raise HTTPException(409, "航次所属船舶不可更改，请为目标船舶新建航次")
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
    old_applicants = db.scalars(
        select(TemporaryEntryApplicant).where(TemporaryEntryApplicant.voyage_id == voyage_id)
    ).all()
    for applicant in old_applicants:
        db.delete(applicant)
    old_exit_stamp_applicants = db.scalars(
        select(ExitStampApplicant).where(ExitStampApplicant.voyage_id == voyage_id)
    ).all()
    for applicant in old_exit_stamp_applicants:
        db.delete(applicant)
    previous = db.scalars(select(CrewManifest).where(CrewManifest.voyage_id == voyage_id).order_by(CrewManifest.version.desc())).first()
    manifest = CrewManifest(voyage_id=voyage_id, source_filename=file.filename, source_type=meta["source_type"], version=(previous.version + 1 if previous else 1))
    db.add(manifest)
    db.flush()
    for row in members:
        db.add(CrewMember(manifest_id=manifest.id, name=row["name"], gender=row["gender"], nationality=row["nationality"], birth_date=row["birth_date"], document_no=row["document_no"], rank=row["rank"], extra_json=json.dumps(row["extra"], ensure_ascii=False)))
    db.commit()
    return {"manifest_id": manifest.id, "version": manifest.version, "count": len(members), "meta": meta}


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


def _latest_manifest(db: Session, voyage_id: int):
    return db.scalars(
        select(CrewManifest).where(CrewManifest.voyage_id == voyage_id).order_by(CrewManifest.version.desc())
    ).first()


def _temporary_entry_row(item, crew_member):
    return {
        "id": item.id,
        "crew_member_id": crew_member.id,
        "name": crew_member.name,
        "nationality": crew_member.nationality,
        "rank": crew_member.rank,
        "birth_date": crew_member.birth_date.isoformat() if crew_member.birth_date else None,
        "document_no": crew_member.document_no,
    }


@app.post("/api/voyages/{voyage_id}/temporary-entry")
def add_temporary_entry_applicant(voyage_id: int, payload: TemporaryEntryApplicantCreate, db: Session = Depends(get_db)):
    if not db.get(Voyage, voyage_id):
        raise HTTPException(404, "航次不存在")
    manifest = _latest_manifest(db, voyage_id)
    crew_member = db.get(CrewMember, payload.crew_member_id)
    if not manifest or not crew_member or crew_member.manifest_id != manifest.id:
        raise HTTPException(400, "只能从当前航次最新船员名单中选择人员")
    existing = db.scalars(
        select(TemporaryEntryApplicant).where(
            TemporaryEntryApplicant.voyage_id == voyage_id,
            TemporaryEntryApplicant.crew_member_id == crew_member.id,
        )
    ).first()
    if existing:
        raise HTTPException(409, "该船员已在临入申请名单中")
    item = TemporaryEntryApplicant(voyage_id=voyage_id, crew_member_id=crew_member.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _temporary_entry_row(item, crew_member)


@app.delete("/api/temporary-entry/{applicant_id}")
def delete_temporary_entry_applicant(applicant_id: int, db: Session = Depends(get_db)):
    item = db.get(TemporaryEntryApplicant, applicant_id)
    if not item:
        raise HTTPException(404, "临入申请人员不存在")
    db.delete(item)
    db.commit()
    return {"ok": True, "id": applicant_id}


def _exit_stamp_row(item, crew_member):
    return {
        "id": item.id,
        "crew_member_id": crew_member.id,
        "name": crew_member.name,
        "nationality": crew_member.nationality,
        "rank": crew_member.rank,
        "birth_date": crew_member.birth_date.isoformat() if crew_member.birth_date else None,
        "document_no": crew_member.document_no,
    }


@app.post("/api/voyages/{voyage_id}/exit-stamp")
def add_exit_stamp_applicant(voyage_id: int, payload: ExitStampApplicantCreate, db: Session = Depends(get_db)):
    if not db.get(Voyage, voyage_id):
        raise HTTPException(404, "航次不存在")
    manifest = _latest_manifest(db, voyage_id)
    crew_member = db.get(CrewMember, payload.crew_member_id)
    if not manifest or not crew_member or crew_member.manifest_id != manifest.id:
        raise HTTPException(400, "只能从当前航次最新船员名单中选择人员")
    existing = db.scalars(
        select(ExitStampApplicant).where(
            ExitStampApplicant.voyage_id == voyage_id,
            ExitStampApplicant.crew_member_id == crew_member.id,
        )
    ).first()
    if existing:
        raise HTTPException(409, "该船员已在出境章申请名单中")
    item = ExitStampApplicant(voyage_id=voyage_id, crew_member_id=crew_member.id)
    db.add(item)
    db.commit()
    db.refresh(item)
    return _exit_stamp_row(item, crew_member)


@app.delete("/api/exit-stamp/{applicant_id}")
def delete_exit_stamp_applicant(applicant_id: int, db: Session = Depends(get_db)):
    item = db.get(ExitStampApplicant, applicant_id)
    if not item:
        raise HTTPException(404, "出境章申请人员不存在")
    db.delete(item)
    db.commit()
    return {"ok": True, "id": applicant_id}


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


@app.get("/api/voyages/{voyage_id}/export/maritime-preapproval")
def export_maritime_preapproval_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    output = export_maritime_preapproval(db.get(Vessel, voyage.vessel_id), voyage)
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


@app.get("/api/voyages/{voyage_id}/export/temporary-entry")
def export_temporary_entry_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    applicants = db.scalars(
        select(TemporaryEntryApplicant).where(TemporaryEntryApplicant.voyage_id == voyage_id).order_by(TemporaryEntryApplicant.id)
    ).all()
    if not applicants:
        raise HTTPException(400, "请先加入申请临入人员")
    crew = [db.get(CrewMember, item.crew_member_id) for item in applicants]
    crew = [item for item in crew if item]
    if not crew:
        raise HTTPException(400, "临入申请名单中的船员资料不存在")
    output = export_temporary_entry(db.get(Vessel, voyage.vessel_id), voyage, crew)
    return FileResponse(
        output,
        filename=output.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/api/voyages/{voyage_id}/export/exit-stamp")
def export_exit_stamp_application_form(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    applicants = db.scalars(
        select(ExitStampApplicant).where(ExitStampApplicant.voyage_id == voyage_id).order_by(ExitStampApplicant.id)
    ).all()
    if not applicants:
        raise HTTPException(400, "请先加入出境章申请人员")
    crew = [db.get(CrewMember, item.crew_member_id) for item in applicants]
    crew = [item for item in crew if item]
    if not crew:
        raise HTTPException(400, "出境章申请名单中的船员资料不存在")
    output = export_exit_stamp_application(db.get(Vessel, voyage.vessel_id), voyage, crew)
    return FileResponse(
        output,
        filename=output.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/api/voyages/{voyage_id}/summary")
def summary(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    manifest = db.scalars(select(CrewManifest).where(CrewManifest.voyage_id == voyage_id).order_by(CrewManifest.version.desc())).first()
    crew = db.scalars(select(CrewMember).where(CrewMember.manifest_id == manifest.id)).all() if manifest else []
    changes = db.scalars(select(CrewChangePerson).where(CrewChangePerson.voyage_id == voyage_id)).all()
    temporary_entries = db.scalars(
        select(TemporaryEntryApplicant).where(TemporaryEntryApplicant.voyage_id == voyage_id).order_by(TemporaryEntryApplicant.id)
    ).all()
    exit_stamp_applicants = db.scalars(
        select(ExitStampApplicant).where(ExitStampApplicant.voyage_id == voyage_id).order_by(ExitStampApplicant.id)
    ).all()
    tonnage = db.scalars(select(TonnageApplication).where(TonnageApplication.voyage_id == voyage_id)).first()
    latest = db.scalars(select(Forecast).where(Forecast.voyage_id == voyage_id).order_by(Forecast.version.desc())).first()
    from collections import Counter
    nationality_stats = Counter(x.nationality or "待人工填写" for x in crew)
    female_count = sum(1 for x in crew if (x.gender or "").strip().lower() in {"女", "female", "f"})
    previous = normalize_port(voyage.previous_port, voyage.previous_port_country)
    next_port = normalize_port(voyage.next_port, voyage.next_port_country)
    port_sequence = "-".join(x for x in [previous if voyage.previous_port else None, "南沙", next_port if voyage.next_port else None] if x)
    summary_keywords = {
        "vessel_chinese_name": vessel.chinese_name or "待人工填写",
        "vessel_english_name": vessel.english_name or "待人工填写",
        "imo": vessel.imo or "待人工填写",
        "vessel_nationality": vessel.nationality or "待人工填写",
        "inbound_voyage_no": voyage.inbound_voyage_no or "待人工填写",
        "outbound_voyage_no": voyage.outbound_voyage_no or "待人工填写",
        "berth": berth_text(voyage.berth) if voyage.berth else "待人工填写",
        "port_sequence": port_sequence or "待人工填写",
        "arrival_time": voyage.arrival_time.strftime("%Y-%m-%d %H:%M") if voyage.arrival_time else "待人工填写",
        "departure_time": voyage.departure_time.strftime("%Y-%m-%d %H:%M") if voyage.departure_time else "待人工填写",
        "crew_count": len(crew),
        "nationality_distribution": "、".join(f"{name}{count}名" for name, count in nationality_stats.items()) or "待人工填写",
        "female_count": female_count,
    }
    crew_rows = [{"id": x.id, "name": x.name, "gender": x.gender, "nationality": x.nationality, "birth_date": x.birth_date.isoformat() if x.birth_date else None, "document_no": x.document_no, "rank": x.rank} for x in crew]
    change_rows = [{"id": x.id, "direction": x.direction, "name": x.name, "nationality": x.nationality, "gender": x.gender, "birth_date": x.birth_date.isoformat() if x.birth_date else None, "document_no": x.document_no, "rank": x.rank, "reason": x.reason, "temporary_entry_permit": x.temporary_entry_permit, "flight_no": x.flight_no, "flight_time": x.flight_time.isoformat() if x.flight_time else None, "route": x.route} for x in changes]
    temporary_entry_rows = [_temporary_entry_row(item, member) for item in temporary_entries if (member := db.get(CrewMember, item.crew_member_id))]
    exit_stamp_rows = [_exit_stamp_row(item, member) for item in exit_stamp_applicants if (member := db.get(CrewMember, item.crew_member_id))]
    return {
        "voyage": voyage_dict(voyage), "vessel": vessel_dict(vessel), "crew_count": len(crew),
        "captain": next((x.name for x in crew if (x.rank or "").lower() in {"船长", "master", "1-船长"}), None),
        "nationality_stats": dict(Counter(x.nationality or "待人工填写" for x in crew)),
        "gender_stats": dict(Counter(x.gender or "待人工填写" for x in crew)), "crew": crew_rows, "crew_change": change_rows, "temporary_entry": temporary_entry_rows, "exit_stamp": exit_stamp_rows, "summary_keywords": summary_keywords,
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
