"""Company website enrichment API."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Company
from app.deps import get_current_user, audit
from app.website_enricher import enrich_companies_batch, enrich_company_website

router = APIRouter(prefix="/api", tags=["enrich"])


@router.post("/enrich/website/{company_id}")
def enrich_single(company_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """Visit a company's website and extract additional contacts."""
    company = db.query(Company).filter(
        Company.id == company_id, Company.user_id == user.id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    result = enrich_company_website(company, db, user.id)
    audit(db, user.id, "website_enrich", "company", company_id,
          "%d emails, %d phones" % (len(result["emails"]), len(result["phones"])))
    db.commit()
    return result


@router.post("/enrich/batch")
def enrich_batch(payload: dict, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """Enrich multiple companies by visiting their websites."""
    company_ids = payload.get("company_ids", [])
    if not company_ids:
        companies = db.query(Company).filter(
            Company.user_id == user.id,
            Company.website.isnot(None)).limit(50).all()
        company_ids = [c.id for c in companies]

    if not company_ids:
        raise HTTPException(status_code=404, detail="No companies with websites to enrich")

    result = enrich_companies_batch(db, user.id, company_ids)
    audit(db, user.id, "website_enrich_batch", "enrich", None,
          "%d enriched, %d emails found" % (result["enriched"], result["emails_found"]))
    db.commit()
    return result
