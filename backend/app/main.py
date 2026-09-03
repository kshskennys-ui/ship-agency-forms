import json
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine, get_db
from .models import AppSetting, CrewChangePerson, CrewManifest, CrewMember, ExitStampApplicant, Forecast, PreferentialCountry, SeafarerVerification, TemporaryEntryApplicant, TonnageApplication, Vessel, Voyage
from .paths import FRONTEND_DIR
from .schemas import CrewChangeCreate, CrewChangePersonUpdate, ExitStampApplicantCreate, PreferentialCountryCreate, TextExtractRequest, TemporaryEntryApplicantCreate, TonnageCreate, VesselCreate, VoyageCreate, VoyageUpdate
from .services.forecast import berth_text, generate_forecast, normalize_port
from .services.importers import parse_crew_file
from .services.exporters import export_border_inspection, export_crew_change, export_crew_change_customs, export_exit_stamp_application, export_health_declaration, export_inbound_form, export_maritime_preapproval, export_outer_field_receipt, export_temporary_entry, export_tonnage
from .services.text_extractor import parse_fixed_text
from .services.seafarer_verifier import crew_verification_row, current_job, eligibility, start_job, stop_job
from .services.tonnage_rates import PREFERENTIAL_COUNTRIES, build_tonnage_text, calculate_tonnage_quote, decimal_text, normalize_country_name


Base.metadata.create_all(engine)


def _ensure_schema():
    """Apply the small additive migrations needed by the SQLite MVP database."""
    columns = {column["name"] for column in inspect(engine).get_columns("voyages")}
    if "customs_inspection" not in columns:
        with engine.begin() as connection:
            default_value = "FALSE" if engine.dialect.name == "postgresql" else "0"
            connection.execute(text(f"ALTER TABLE voyages ADD COLUMN customs_inspection BOOLEAN NOT NULL DEFAULT {default_value}"))
    tonnage_columns = {column["name"] for column in inspect(engine).get_columns("tonnage_applications")}
    tonnage_additions = {
        "unit_price": "VARCHAR(32)",
        "tax_type": "VARCHAR(32)",
        "net_tonnage": "INTEGER",
        "generated_text": "TEXT",
    }
    missing_tonnage_columns = {name: definition for name, definition in tonnage_additions.items() if name not in tonnage_columns}
    if missing_tonnage_columns:
        with engine.begin() as connection:
            for name, definition in missing_tonnage_columns.items():
                connection.execute(text(f"ALTER TABLE tonnage_applications ADD COLUMN {name} {definition}"))


_ensure_schema()


def _seed_preferential_countries():
    """首次初始化优惠国家；后续允许用户删空，不因重启自动恢复。"""
    with SessionLocal() as db:
        marker = db.get(AppSetting, "preferential_countries_seeded")
        if marker:
            return
        if not db.scalars(select(PreferentialCountry)).first():
            db.add_all(PreferentialCountry(name=name) for name in sorted(PREFERENTIAL_COUNTRIES))
        db.add(AppSetting(key="preferential_countries_seeded", value="1"))
        db.commit()


_seed_preferential_countries()
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


def preferential_country_names(db: Session):
    return {item.name for item in db.scalars(select(PreferentialCountry).order_by(PreferentialCountry.name)).all()}


@app.get("/api/settings/preferential-countries")
def list_preferential_countries(db: Session = Depends(get_db)):
    return [{"id": item.id, "name": item.name} for item in db.scalars(select(PreferentialCountry).order_by(PreferentialCountry.name)).all()]


@app.post("/api/settings/preferential-countries")
def add_preferential_country(payload: PreferentialCountryCreate, db: Session = Depends(get_db)):
    name = normalize_country_name(payload.name)
    if not name:
        raise HTTPException(400, "国家名不能为空")
    if db.scalars(select(PreferentialCountry).where(PreferentialCountry.name == name)).first():
        raise HTTPException(409, "该国家已在优惠国家名单中")
    item = PreferentialCountry(name=name)
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "name": item.name}


@app.delete("/api/settings/preferential-countries/{country_id}")
def delete_preferential_country(country_id: int, db: Session = Depends(get_db)):
    item = db.get(PreferentialCountry, country_id)
    if not item:
        raise HTTPException(404, "优惠国家不存在")
    db.delete(item)
    db.commit()
    return {"ok": True, "id": country_id}


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
    # 船舶证书编号属于档案扩展字段，编辑已有档案时也必须一并保存。
    item.extra_json = json.dumps(payload.extra, ensure_ascii=False)
    db.commit()
    db.refresh(item)
    return vessel_dict(item)


def remove_voyage_records(db: Session, voyage_id: int):
    """删除航次及其关联业务记录，供航次删除和船舶级联删除共用。"""
    verifications = db.scalars(select(SeafarerVerification).where(SeafarerVerification.voyage_id == voyage_id)).all()
    for verification in verifications:
        db.delete(verification)
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


def _seafarer_verification_rows(db: Session, voyage_id: int):
    manifest = _latest_manifest(db, voyage_id)
    if not manifest:
        return []
    members = db.scalars(
        select(CrewMember).where(CrewMember.manifest_id == manifest.id).order_by(CrewMember.id)
    ).all()
    verifications = db.scalars(
        select(SeafarerVerification).where(SeafarerVerification.voyage_id == voyage_id)
    ).all()
    by_member_id = {item.crew_member_id: item for item in verifications}
    return [crew_verification_row(member, by_member_id.get(member.id)) for member in members]


@app.get("/api/voyages/{voyage_id}/seafarer-verification")
def get_seafarer_verification(voyage_id: int, db: Session = Depends(get_db)):
    if not db.get(Voyage, voyage_id):
        raise HTTPException(404, "航次不存在")
    rows = _seafarer_verification_rows(db, voyage_id)
    eligible_rows = [row for row in rows if row["eligible"]]
    job = current_job(voyage_id)
    return {
        "items": rows,
        "eligible_count": len(eligible_rows),
        "completed_count": sum(1 for row in eligible_rows if row["status"] in {"有效", "无效"}),
        "job": job,
    }


@app.post("/api/voyages/{voyage_id}/seafarer-verification/start")
def start_seafarer_verification(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    existing_job = current_job(voyage_id)
    if existing_job and existing_job["status"] in {"排队中", "查询中"}:
        return existing_job
    manifest = _latest_manifest(db, voyage_id)
    if not manifest:
        raise HTTPException(400, "请先导入船员名单")
    members = db.scalars(
        select(CrewMember).where(CrewMember.manifest_id == manifest.id).order_by(CrewMember.id)
    ).all()
    eligible_members = [member for member in members if eligibility(member)[0]]
    if not eligible_members:
        raise HTTPException(400, "当前名单中没有可自动核验的中国籍海员证人员")

    rows = []
    for member in eligible_members:
        verification = db.scalars(
            select(SeafarerVerification).where(
                SeafarerVerification.voyage_id == voyage_id,
                SeafarerVerification.crew_member_id == member.id,
            )
        ).first()
        if not verification:
            verification = SeafarerVerification(voyage_id=voyage_id, crew_member_id=member.id)
            db.add(verification)
        verification.status = "待查询"
        verification.website_certificate_no = None
        verification.website_name = None
        verification.certificate_status = None
        verification.issuing_authority = None
        verification.issue_date = None
        verification.valid_date = None
        verification.error_info = None
        verification.attempts = 0
        verification.queried_at = None
        rows.append({
            "crew_member_id": member.id,
            "name": member.name,
            "nationality": member.nationality,
            "rank": member.rank,
            "document_no": member.document_no,
        })
    db.commit()
    return start_job(voyage_id, rows)


@app.post("/api/voyages/{voyage_id}/seafarer-verification/stop")
def stop_seafarer_verification(voyage_id: int, db: Session = Depends(get_db)):
    if not db.get(Voyage, voyage_id):
        raise HTTPException(404, "航次不存在")
    job = stop_job(voyage_id)
    if not job:
        raise HTTPException(400, "当前没有正在运行的海员证核验任务")
    return job


@app.post("/api/voyages/{voyage_id}/seafarer-verification/local-result")
def save_local_seafarer_result(voyage_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """保存用户电脑本地核验助手返回的单人结果。"""
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    manifest = _latest_manifest(db, voyage_id)
    if not manifest:
        raise HTTPException(400, "当前航次没有船员名单")
    try:
        member_id = int(payload.get("crew_member_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "核验结果缺少船员编号")
    member = db.get(CrewMember, member_id)
    if not member or member.manifest_id != manifest.id or not eligibility(member)[0]:
        raise HTTPException(400, "核验结果不属于当前航次可核验人员")
    verification = db.scalars(
        select(SeafarerVerification).where(
            SeafarerVerification.voyage_id == voyage_id,
            SeafarerVerification.crew_member_id == member_id,
        )
    ).first()
    if not verification:
        verification = SeafarerVerification(voyage_id=voyage_id, crew_member_id=member_id)
        db.add(verification)
    allowed_keys = ("status", "website_certificate_no", "website_name", "certificate_status", "issuing_authority", "issue_date", "valid_date", "error_info", "attempts")
    for key in allowed_keys:
        if key in payload:
            setattr(verification, key, payload.get(key))
    verification.queried_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "crew_member_id": member_id}


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
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    if not vessel:
        raise HTTPException(400, "当前航次未绑定船舶档案")
    if not payload.purchase_date:
        raise HTTPException(400, "请填写吨税起购日期")
    try:
        quote = calculate_tonnage_quote(vessel.nationality, vessel.net_tonnage, payload.duration_days, preferential_country_names(db))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    item = db.scalars(select(TonnageApplication).where(TonnageApplication.voyage_id == voyage_id)).first()
    if not item:
        item = TonnageApplication(voyage_id=voyage_id)
        db.add(item)
    item.amount = decimal_text(quote["total_amount"])
    item.unit_price = decimal_text(quote["unit_price"])
    item.tax_type = quote["tax_type"]
    item.net_tonnage = int(vessel.net_tonnage)
    item.generated_text = build_tonnage_text(vessel, voyage, payload.purchase_date, quote)
    item.pre_entry_no = payload.pre_entry_no
    item.duration_days = payload.duration_days
    item.purchase_date = payload.purchase_date
    item.charter_relation = payload.charter_relation
    db.commit()
    return {
        "ok": True,
        "id": item.id,
        "amount": item.amount,
        "unit_price": item.unit_price,
        "tax_type": item.tax_type,
        "net_tonnage": item.net_tonnage,
        "generated_text": item.generated_text,
    }


@app.get("/api/voyages/{voyage_id}/tonnage")
def get_tonnage(voyage_id: int, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    item = db.scalars(select(TonnageApplication).where(TonnageApplication.voyage_id == voyage_id)).first()
    if not item:
        return {"exists": False}
    return {
        "exists": True,
        "amount": item.amount,
        "unit_price": item.unit_price,
        "tax_type": item.tax_type,
        "net_tonnage": item.net_tonnage,
        "pre_entry_no": item.pre_entry_no,
        "duration_days": item.duration_days,
        "purchase_date": item.purchase_date.isoformat() if item.purchase_date else None,
        "charter_relation": item.charter_relation,
        "generated_text": item.generated_text,
    }


@app.get("/api/voyages/{voyage_id}/tonnage-quote")
def get_tonnage_quote(voyage_id: int, duration_days: int | None = None, purchase_date: date | None = None, db: Session = Depends(get_db)):
    voyage = db.get(Voyage, voyage_id)
    if not voyage:
        raise HTTPException(404, "航次不存在")
    vessel = db.get(Vessel, voyage.vessel_id)
    if not vessel:
        raise HTTPException(400, "当前航次未绑定船舶档案")
    try:
        quote = calculate_tonnage_quote(vessel.nationality, vessel.net_tonnage, duration_days, preferential_country_names(db))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    generated_text = build_tonnage_text(vessel, voyage, purchase_date, quote) if purchase_date else None
    return {
        "vessel_chinese_name": vessel.chinese_name,
        "vessel_english_name": vessel.english_name,
        "vessel_nationality": vessel.nationality,
        "inbound_voyage_no": voyage.inbound_voyage_no,
        "net_tonnage": vessel.net_tonnage,
        "tax_type": quote["tax_type"],
        "preferential": quote["preferential"],
        "tier_index": quote["tier_index"],
        "tonnage_tier": quote["tier"],
        "duration_days": quote["duration_days"],
        "duration_text": quote["duration_text"],
        "unit_price": decimal_text(quote["unit_price"]),
        "total_amount": decimal_text(quote["total_amount"]),
        "generated_text": generated_text,
    }


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
        "tonnage": {"amount": tonnage.amount, "unit_price": tonnage.unit_price, "tax_type": tonnage.tax_type, "net_tonnage": tonnage.net_tonnage, "pre_entry_no": tonnage.pre_entry_no, "duration_days": tonnage.duration_days, "purchase_date": tonnage.purchase_date.isoformat() if tonnage.purchase_date else None, "generated_text": tonnage.generated_text} if tonnage else None,
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
