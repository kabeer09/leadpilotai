"""Multi-agent research and qualification API."""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Company, Lead
from app.deps import get_current_user, audit
from app.agents import run_research_pipeline

router = APIRouter(prefix="/api", tags=["research"])


@router.post("/research/run")
def run_research(
    payload: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run the research pipeline on selected companies."""
    company_ids = payload.get("company_ids", [])
    if not company_ids:
        # Default: research all companies without website research
        companies = db.query(Company).filter(
            Company.user_id == user.id).limit(50).all()
        company_ids = [c.id for c in companies]

    if not company_ids:
        raise HTTPException(status_code=404, detail="No companies to research")

    # Run synchronously (for now; can be made async later)
    result = run_research_pipeline(db, user.id, company_ids)
    audit(db, user.id, "research_run", "research", None,
          f"{result['companies_processed']} processed")
    db.commit()

    return result


@router.post("/research/company/{company_id}")
def research_single_company(
    company_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Research a single company's website."""
    company = db.query(Company).filter(
        Company.id == company_id, Company.user_id == user.id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    from app.agents import research_company_website, qualify_buyer, validate_email_contact
    research = research_company_website(company, db)
    qualification = qualify_buyer(company, research)

    return {
        "company_id": company.id,
        "company_name": company.name,
        "research": research,
        "qualification": qualification,
    }


@router.get("/research/qualified")
def list_qualified(
    min_score: int = 40,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List companies with qualification scores above threshold."""
    companies = db.query(Company).filter(Company.user_id == user.id).all()
    results = []
    for c in companies:
        from app.agents import research_company_website, qualify_buyer
        research = research_company_website(c, db)
        qual = qualify_buyer(c, research)
        if qual["score"] >= min_score:
            results.append({
                "company_id": c.id,
                "name": c.name,
                "website": c.website,
                "score": qual["score"],
                "buyer_type": qual["buyer_type"],
                "product_fit": qual["product_fit"],
                "country_fit": qual["country_fit"],
                "recommended_action": qual["recommended_next_action"],
                "missing_evidence": qual["missing_evidence"],
                "emails": [e["value"] for e in research.get("emails", [])],
                "phones": [p["value"] for p in research.get("phones", [])],
            })
    results.sort(key=lambda x: x["score"], reverse=True)
    return {"count": len(results), "companies": results}


@router.post("/research/draft/{company_id}")
def generate_draft(
    company_id: int,
    payload: dict = {},
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate an outreach draft for a company (no sending). Uses Gemini if available."""
    company = db.query(Company).filter(
        Company.id == company_id, Company.user_id == user.id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    from app.agents import research_company_website, qualify_buyer, generate_outreach_draft
    from app.config import settings
    research = research_company_website(company, db)
    qualification = qualify_buyer(company, research)
    gemini_key = payload.get("gemini_key") or settings.GEMINI_API_KEY
    draft = generate_outreach_draft(company, qualification, research, gemini_key)

    return {
        "company_id": company.id,
        "company_name": company.name,
        "draft": draft,
        "ai_available": bool(gemini_key),
    }
