import os
from datetime import datetime, timezone

from mistralai.workflows import run_worker, workflow

from kn_client import KNClient


@workflow
def shipment_briefing(input_data) -> dict:
    """Briefing quotidien des expéditions Kuehne+Nagel (myKN Track API v2)."""
    client = KNClient(
        client_id=os.environ["KN_CLIENT_ID"],
        client_secret=os.environ["KN_CLIENT_SECRET"],
    )

    # 1. Expéditions actives : réservées ou en transit
    search = client.search_shipments(stages=["BOOKED", "IN_TRANSIT"])
    abstracts = extract_abstracts(search)

    # 2. Détails de chaque expédition (ETA, jalons, retards)
    details = []
    for abstract in abstracts:
        tracking_number = abstract.get("trackingNumber")
        if not tracking_number:
            continue
        try:
            details.append(client.get_shipment_details(tracking_number))
        except Exception:
            details.append({"trackingNumber": tracking_number, "error": True})

    # 3. Consolidation et rapport
    report = build_report(abstracts, details)
    return {"report": report, "generated_at": datetime.now(timezone.utc).isoformat()}


def extract_abstracts(search_response) -> list:
    if isinstance(search_response, list):
        return search_response
    for key in ("shipments", "content", "results"):
        if isinstance(search_response, dict) and key in search_response:
            return search_response[key] or []
    return []


def build_report(abstracts: list, details: list) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()

    by_stage = {}
    for s in abstracts:
        by_stage.setdefault(s.get("currentStage", "UNKNOWN"), []).append(s.get("trackingNumber"))

    delayed = []
    shipments = []
    for d in details:
        routing = d.get("routing") or {}
        milestone_info = routing.get("milestoneInfo") or {}
        stage = milestone_info.get("currentStage")
        current_milestone = milestone_info.get("currentMilestone")

        arrival = ((routing.get("arrivalDate") or {}).get("dateTime") or {})
        planned_arrival = arrival.get("planned")
        is_delayed = bool(
            stage == "IN_TRANSIT" and planned_arrival and planned_arrival < now_iso
        )
        if is_delayed:
            delayed.append(d.get("trackingNumber"))

        shipments.append({
            "trackingNumber": d.get("trackingNumber"),
            "mode": d.get("modeOfTransport"),
            "stage": stage,
            "currentMilestone": current_milestone,
            "plannedArrival": planned_arrival,
            "delayed": is_delayed,
            "error": bool(d.get("error")),
        })

    return {
        "total_active": len(abstracts),
        "by_stage": {k: len(v) for k, v in by_stage.items()},
        "delayed": delayed,
        "shipments": shipments,
    }


if __name__ == "__main__":
    run_worker([shipment_briefing])
