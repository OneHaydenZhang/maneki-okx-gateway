"""Walk the OKX.AI marketplace flow exactly as a user's agent would (onchainos CLI)."""
import base64, json, subprocess, sys, time

def cli(*args, tries=3):
    for i in range(tries):
        p = subprocess.run(["onchainos", *args], capture_output=True, text=True, timeout=180)
        out = p.stdout.strip().splitlines()
        last = out[-1] if out else ""
        try:
            d = json.loads(last)
            if d.get("ok") or "data" in d:
                return d
            err = d.get("error", "")
            if "unreachable" not in str(err) and "sending request" not in str(err):
                return d
        except ValueError:
            pass
        time.sleep(4)
    return d if 'd' in dir() else {"ok": False, "error": p.stderr[-300:]}

def b64(obj): return base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()

def brief(d, n=600):
    return json.dumps(d, ensure_ascii=False)[:n]

# 1) discover
m = cli("agent", "service-match", "--keywords", "Maneki", "--limit", "10")
svcs = (m.get("data") or {}).get("services") or []
by = {s.get("serviceName"): s for s in svcs}
print("== 1 service-match:", len(svcs), "hits:", [s.get("serviceName") for s in svcs])
def sid(name): return by[name].get("sid") or by[name].get("serviceId")

def invoke(name, params):
    """task-create-prepare → a2mcp-probe (→ pay or confirm-free) → result"""
    prep = cli("agent", "task-create-prepare", "--sid", str(sid(name)))
    d = prep.get("data") or prep
    phase, decision, reason = d.get("phase"), d.get("decision"), d.get("reason")
    print(f"\n== {name}: prepare → {phase}/{decision}/{reason}")
    routing = d.get("payload") or {}
    pr = cli("agent", "a2mcp-probe", "probe", "--routing-base64", b64(routing), "--params-base64", b64(params))
    pd = pr.get("data") or pr
    phase, reason = pd.get("phase"), pd.get("reason")
    print(f"   probe → {phase}/{reason}")
    payload = pd.get("payload") or {}
    if phase == "endpoint_result":
        print("   result:", brief(payload.get("result"), 500)); return payload.get("result")
    if phase == "parameter_collection":
        print("   input_required fields:", [f.get("name") for f in (payload.get("fields") or [])]); return None
    if phase == "payment_confirmation" and reason == "free_confirmation_required":
        cid = payload.get("confirmationId") or (pd.get("nextAction") or [{}])[0].get("params", {}).get("confirmationId")
        cf = cli("agent", "a2mcp-probe", "confirm-free", "--confirmation-id", str(cid), "--yes")
        cd = cf.get("data") or cf
        print(f"   confirm-free → {cd.get('phase')}/{cd.get('reason')}")
        res = (cd.get("payload") or {}).get("result")
        print("   result:", brief(res, 700)); return res
    if phase == "payment_confirmation":
        pres = payload.get("presentation") or {}
        cands = payload.get("candidates") or []
        print("   card:", {k: pres.get(k) for k in ("serviceProvider", "serviceName", "fee")}, "| candidates:",
              [(c.get("candidateId"), c.get("tokenSymbol"), c.get("amountDisplay"), c.get("balanceStatus")) for c in cands])
        prepared = payload.get("preparedId")
        cand = next((c for c in cands if c.get("balanceStatus") in ("sufficient", "ok")), cands[0] if cands else None)
        pp = cli("agent", "a2mcp-probe", "prepare-payment", "--prepared-id", str(prepared), "--candidate-id", str(cand.get("candidateId")), "--yes")
        ppd = pp.get("data") or pp
        print(f"   prepare-payment → {ppd.get('phase')}/{ppd.get('reason')}")
        pay_id = ((ppd.get("payload") or {}).get("paymentId")) or next((a.get("params", {}).get("paymentId") for a in ppd.get("nextAction") or [] if a.get("id") == "execute_a2mcp_payment"), None)
        print("   paymentId:", pay_id)
        if pay_id:
            pay = cli("agent", "a2mcp-probe", "pay", "--payment-id", str(pay_id), "--yes") if False else cli("payment", "pay", "--payment-id", str(pay_id), "--yes")
            pdd = pay.get("data") or pay
            print("   pay →", pdd.get("status"), "tx", (pdd.get("txHash") or "")[:18], "| result:", brief(pdd.get("result"), 500))
            return pdd.get("result")
    print("   unexpected:", brief(pd, 800))
    return None



api_key = "mk_zzBjR-2xEw2_PRoWU3S8xRe5uD4Jg7o0"
cr = invoke("Maneki Virtual Trading Agent", {"api_key": api_key, "symbol": "AAPL", "persona": "balanced", "capital_max": 150, "max_ticks": 6})
aid = (cr or {}).get("agent", {}).get("agent_id") if isinstance(cr, dict) else None
print("\ncreated agent:", aid)
if aid:
    invoke("Maneki Agent Control", {"api_key": api_key, "agent_id": aid, "action": "stop"})
