from __future__ import annotations


def ingest_session_ack_once(ack: dict) -> dict:
    from blender.ui.state_view import get_session

    session = get_session()
    ok = session.ingest_session_ack(ack)
    truth = session.get_truth_state()
    return {
        "ok": ok,
        "reason": None if ok else session.get_last_error(),
        "state": {
            "local_ready": truth["local_ready"],
            "connect_attempted": truth["connect_attempted"],
            "counterpart_observed": truth["counterpart_observed"],
            "handshake_confirmed": truth["handshake_confirmed"],
            "current_handshake_id": truth.get("current_handshake_id"),
        },
    }


def ingest_final_handshake_ack_once(ack: dict) -> dict:
    from blender.ui.state_view import get_session

    session = get_session()
    ok = session.ingest_final_handshake_ack(ack)
    truth = session.get_truth_state()
    return {
        "ok": ok,
        "reason": None if ok else session.get_last_error(),
        "state": {
            "local_ready": truth["local_ready"],
            "connect_attempted": truth["connect_attempted"],
            "counterpart_observed": truth["counterpart_observed"],
            "handshake_confirmed": truth["handshake_confirmed"],
            "current_handshake_id": truth.get("current_handshake_id"),
        },
    }

