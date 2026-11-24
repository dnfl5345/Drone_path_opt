"""DJI Matrice 4T 전력 모델 요약 → results/m4t_model.json (보고서·발표자료가 읽는다).

    PYTHONPATH=src python scripts/m4t_model_summary.py
"""
import json
from pathlib import Path

import numpy as np

from dpo import drones as DR
from dpo.energy import RHO0, power_w, soc_drop

ROOT = Path(__file__).resolve().parents[1]


def main():
    d = DR.get("m4t", calibrated=False)
    pr = d.params()
    S = DR.M4T_SPEC
    out = dict(spec=S, eta=d.eta, cda=d.cda, p0=d.p0, p_av=d.p_av, area=d.area, u_tip=d.u_tip, va_max=d.va_max, vg_max=d.vg_max,
               vz_up=d.vz_up, vz_dn=d.vz_dn, by_payload={})
    vs = np.arange(3.0, 18.01, 0.5)
    for pl in (0.0, 0.1, 0.2):
        m = d.m_empty + pl
        jm = [power_w(v, 0.0, m, RHO0, pr) / v for v in vs]
        i = int(np.argmin(jm))
        p_h = power_w(0.0, 0.0, m, RHO0, pr)
        out["by_payload"][f"{pl:.1f}"] = dict(
            mass=m, hover_w=p_h, hover_min=100.0 / soc_drop(p_h * 60.0, d), v_best=float(vs[i]), jm_best=float(jm[i]),
            range_km=100.0 / soc_drop(jm[i] * 1000.0, d), p9_w=power_w(9.0, 0.0, m, RHO0, pr), p15_w=power_w(15.0, 0.0, m, RHO0, pr))
    out["check"] = dict(hover_soc=float(soc_drop(power_w(0.0, 0.0, d.m_empty, RHO0, pr) * S["hover_min"] * 60, d)),
                        cruise_soc=float(soc_drop(power_w(S["flight_speed"], 0.0, d.m_empty, RHO0, pr) * S["flight_min"] * 60, d)))
    f = ROOT / "results" / "m4t_model.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out["by_payload"], indent=1), out["check"], round(d.eta, 3), round(d.cda, 4))


if __name__ == "__main__":
    main()
