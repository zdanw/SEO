"""Run baseline init without GSC and print progress."""
import json

from app.core.database import SessionLocal
from app.domains.sites.onboarding_service import OnboardingService
from app.models.client_site import ClientSite

SITE_ID = 1


def main() -> None:
    db = SessionLocal()
    try:
        site = db.get(ClientSite, SITE_ID)
        assert site is not None
        # force start
        site.init_status = "pending"
        db.commit()
        OnboardingService.start_init(db, SITE_ID)
        result = OnboardingService.run_init_pipeline(db, SITE_ID)
        print("init_status=", result["init_status"])
        progress = result["progress"]
        assert "gsc_sync" not in progress
        for step, info in progress.items():
            if step.startswith("_"):
                continue
            print(f"  {step}: {info.get('status')} {info.get('reason') or info.get('error') or ''}")
        assert result["init_status"] == "ready", result
        print("PASS: ready without GSC")
    finally:
        db.close()


if __name__ == "__main__":
    main()
