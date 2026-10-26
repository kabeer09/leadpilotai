"""CSV import pipeline for manual fair sources."""
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Company, Contact, Lead
from app.deps import get_current_user, audit
from app.source_registry import get_source

router = APIRouter(prefix="/api", tags=["import"])


CSV_TEMPLATE_COLUMNS = [
    "company_name", "website", "email", "phone", "address",
    "booth", "hall", "country", "product_categories",
    "exhibitor_profile_url", "source_name",
]


@router.get("/import/template")
def download_template():
    """Return CSV template with headers."""
    import json
    return {"columns": CSV_TEMPLATE_COLUMNS,
            "example": {
                "company_name": "Example Company Ltd",
                "website": "https://example.com",
                "email": "info@example.com",
                "phone": "+1-555-123-4567",
                "address": "123 Main St, City, Country",
                "booth": "A123",
                "hall": "Hall 4",
                "country": "Germany",
                "product_categories": "Kitchenware, Tableware",
                "exhibitor_profile_url": "https://fair.com/exhibitor/123",
                "source_name": "Ambiente Frankfurt",
            }}


@router.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    source_name: str = "Manual Import",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Import companies from CSV. Idempotent by (user, company_name, website)."""
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=422, detail="File must be a .csv")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=422, detail="CSV must have a header row")

    # Normalize field names
    field_map = {f.strip().lower().replace(" ", "_"): f for f in reader.fieldnames}
    required = {"company_name"}
    if not required.issubset(set(field_map.keys())):
        raise HTTPException(
            status_code=422,
            detail="CSV must contain 'company_name' column. "
                   "Download template from /api/import/template")

    imported = 0
    skipped = 0
    errors = []
    for i, row in enumerate(reader, start=2):
        name = (row.get("company_name") or row.get("Company Name") or "").strip()
        if not name:
            skipped += 1
            continue

        website = (row.get("website") or "").strip() or None
        email = (row.get("email") or "").strip() or None
        phone = (row.get("phone") or "").strip() or None
        address = (row.get("address") or "").strip() or None
        booth = (row.get("booth") or "").strip() or None
        hall = (row.get("hall") or "").strip() or None
        country = (row.get("country") or "").strip() or None
        categories = (row.get("product_categories") or "").strip() or None
        profile_url = (row.get("exhibitor_profile_url") or "").strip() or None
        src = (row.get("source_name") or source_name).strip()

        # Idempotency: check if company already exists
        existing = db.query(Company).filter(
            Company.user_id == user.id,
            Company.name == name).first()
        if existing:
            # Update missing fields
            if website and not existing.website:
                existing.website = website
            if address and not existing.address:
                existing.address = address
            if booth and not existing.booth:
                existing.booth = booth
            if hall and not existing.hall:
                existing.hall = hall
            if country and not existing.country:
                existing.country = country
            pass  # product_categories not stored on Company model
            skipped += 1
            continue

        # Create company
        company = Company(
            user_id=user.id, name=name, website=website,
            country=country, address=address, booth=booth, hall=hall,
            lifecycle="active")
        db.add(company)
        db.flush()

        # Add contacts
        if email:
            norm = email.strip().lower()
            db.add(Contact(user_id=user.id, company_id=company.id,
                           kind="EMAIL", value=norm, normalized=norm,
                           source_url=profile_url, is_primary=True))
        if phone:
            norm = phone.strip()
            db.add(Contact(user_id=user.id, company_id=company.id,
                           kind="PHONE", value=norm, normalized=norm,
                           source_url=profile_url, is_primary=True))

        # Create lead
        db.add(Lead(user_id=user.id, company_id=company.id, status="NEW"))
        imported += 1

    db.commit()
    audit(db, user.id, "csv_import", "import", None,
          f"{imported} imported, {skipped} skipped")
    db.commit()

    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors[:20],
        "total_rows": imported + skipped,
    }


@router.post("/import/manual-url")
def import_manual_url(payload: dict, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Add a single company from a manually provided profile URL."""
    url = (payload.get("url") or "").strip()
    name = (payload.get("name") or "").strip()
    if not url or not name:
        raise HTTPException(status_code=422, detail="url and name required")

    from app.security import validate_url
    _, err = validate_url(url)
    if err:
        raise HTTPException(status_code=422, detail=err)

    existing = db.query(Company).filter(
        Company.user_id == user.id, Company.name == name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Company already exists")

    company = Company(user_id=user.id, name=name, website=url,
                     lifecycle="active")
    db.add(company)
    db.flush()

    email = (payload.get("email") or "").strip()
    phone = (payload.get("phone") or "").strip()
    if email:
        db.add(Contact(user_id=user.id, company_id=company.id,
                       kind="EMAIL", value=email, normalized=email.lower(),
                       source_url=url, is_primary=True))
    if phone:
        db.add(Contact(user_id=user.id, company_id=company.id,
                       kind="PHONE", value=phone, normalized=phone,
                       source_url=url, is_primary=True))

    db.add(Lead(user_id=user.id, company_id=company.id, status="NEW"))
    db.commit()
    audit(db, user.id, "manual_url_import", "company", company.id, name)
    db.commit()

    return {"id": company.id, "name": company.name, "status": "created"}


@router.get("/sources")
def list_sources():
    """Return source capability matrix."""
    from app.source_registry import get_all_sources
    return get_all_sources()
