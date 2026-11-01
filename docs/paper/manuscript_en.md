# Risk-Bounded, Wind-Aware Energy-Optimal Spatio-Temporal Path Planning for Delivery Drones with Uncertain Moving Obstacles

**Authors:** [자료필요: 저자·소속·교신저자]
**Target venue:** [확인요망: 투고처 – 후보는 `논문화_가이드.md` §5 참조. 템플릿 확정 후 절 번호·인용 형식 변환]

> 이 원고는 저장소의 코드와 실험 결과(`results/summary.json`, `results/robust_stats.json`)로만 작성했습니다. 수치는 모두 해당 파일에서 가져왔고, 근거가 없는 자리는 `[자료필요]`, `[인용필요]`, `[확인요망]`, `[주장약화]` 마커로 남겼습니다. 참고문헌은 `references.bib`에 있으며, 모두 웹 검색으로 서지를 확인했습니다.

---

## Abstract

Delivery drones must complete round trips on a limited battery while sharing low-altitude airspace with aircraft whose future positions are only predicted. We plan routes that minimise predicted state-of-charge (SOC) consumption in a time-varying 3-D wind field, choosing route, altitude, segment speed and hovering jointly, while bounding the per-instant collision probability against moving obstacles with uncertain trajectories. The prediction uncertainty is modelled as a chance-constrained tube that grows with look-ahead time. We derive its unsafe intervals on a grid in closed form, including the case in which the tube grows faster than the obstacle moves, so that an any-angle safe-interval search with an energy cost (WE-SIPP-Theta\*) can use them directly. Experiments use real terrain, buildings, GFS wind forecasts and a power model validated on 198 recorded flights. Under Gaussian prediction errors (30 scenarios × 1,000 samples), nominal planning collided in 16.4 % of runs. The tube (ε = 0.05) and a constant worst-case buffer both had no collisions, and the tube used 4.9 % less energy (95 % CI 3.7–6.0 %, p = 1.6 × 10⁻⁷). Under heavy-tailed Student-t errors, this advantage disappeared: at matched risk the energy difference was not significant, and at the lowest risk levels the buffer was better. A first-order allocation argument explains this dependence on the error tail. With re-observation every 20 s, the tube reduced replanning by 73 % at a 2.6 % energy cost. The inflation profile should therefore be chosen from the empirical tail of prediction errors.

---

## 1. Introduction

Small multirotor drones are being considered for last-mile parcel delivery, where the usable battery energy, not the path length, decides whether a round trip can be completed with a safe reserve. [인용필요: 드론 배송 운영에서 배터리 제약이 임무 가능성을 결정한다는 실증/정책 문헌] Two features of low-altitude urban airspace make the shortest geometric path a poor proxy for battery use. First, the wind at 30–150 m above ground is strong and varies with altitude and time, so the energy per metre depends on heading, altitude and ground speed. Second, the airspace is shared with other aircraft — other drones, emergency helicopters, temporary flight restrictions — whose future positions are known only through predictions that degrade with the look-ahead time.

Energy-aware planners that account for wind have been proposed on grids [lian2025walpa, lian2025wind] and with any-angle search over CFD wind fields [gu2026wind], and delivery-routing studies have modelled wind- and payload-dependent energy on graphs [bettisorbelli2021wind]. Separately, safe-interval path planning (SIPP) handles moving obstacles with known trajectories [phillips2011sipp, yakovlev2017aasipp], and chance-constrained planning bounds the collision probability under uncertainty [blackmore2011chance, dutoit2012robot, zhu2019chance]. To our knowledge, these lines have not been combined into a single planner that (i) minimises predicted state-of-charge (SOC) consumption in a time-varying 3D wind field, (ii) chooses route, altitude, segment speed and hovering jointly, and (iii) bounds the collision risk against moving obstacles whose predicted trajectories are uncertain. [확인요망: 2024–2026 문헌 추가 조사로 "처음" 주장 범위 재확인 – 현재는 "to our knowledge"로 약화]

This paper addresses that gap. Our central observation is that obstacle-prediction uncertainty grows with look-ahead time, so a constant worst-case buffer — the usual engineering practice — over-constrains the near future where predictions are accurate. We encode the uncertainty as a time-growing chance-constrained tube and show that its safe intervals on a grid can be computed in closed form, which lets an energy-optimal SIPP search use it directly. We also show the limit of this idea: the benefit depends on the tail of the prediction errors, not only on their variance.

**Contributions.**
1. **Formulation.** We pose delivery planning as minimisation of predicted SOC consumption over route, altitude, segment ground speed and waiting time, under a time-varying 3D wind field, leg-specific payload mass and per-time-instant chance constraints against uncertain moving obstacles (Section 3).
2. **Algorithm.** We present WE-SIPP-Theta\*, an any-angle SIPP search with an energy cost. Because the wind makes the energy cost norm non-convex (Section 4.3), shortcuts are accepted only when they are cheaper in energy. We also derive closed-form safe intervals for time-growing uncertainty tubes, including the case where the tube grows faster than the obstacle moves (Section 4.4).
3. **Evidence on real data.** Using real terrain (Copernicus DSM), building footprints (Overture Maps), operational wind forecasts (NOAA GFS) and a power model calibrated on 198 recorded delivery flights of a DJI Matrice 100 [rodrigues2021dataset], we quantify the energy benefit of each planning component and the risk–energy trade-off of uncertainty handling with Monte Carlo evaluation (Sections 5–6).
4. **When the time-growing tube helps.** Under Gaussian prediction errors, the tube matches the empirical safety of a constant worst-case buffer with less energy. Under heavy-tailed errors this advantage disappears. A first-order allocation argument explains why: the optimal inflation grows sublinearly with the prediction scale when the error tail is heavy (Sections 6.4 and 7).

---

## 2. Related Work

**Wind-aware energy-optimal planning.** WA-LPA\* [lian2025walpa] combines an induced/profile/parasitic power model with a wind-alignment heuristic and incremental (lifelong) replanning [koenig2004lpa] on a grid with height layers. A wind-augmented A\* [lian2025wind] adjusts its heuristic with local wind-dependent energy predictions. Gu et al. [gu2026wind] plan with a wind-aware Theta\* over CFD wind fields of a city and report a 7.69 % energy reduction relative to wind-agnostic planning. Betti Sorbelli et al. [bettisorbelli2021wind] model delivery energy as a function of wind and payload on a graph and allow in-flight route changes. From the abstracts we could consult, none of these treats the segment ground speed as a decision variable, or treats moving obstacles with uncertain trajectories. [확인요망: 각 논문 본문에서 속도·장애물 처리 여부 확인 – 현재는 초록 기준]

**Any-angle and safe-interval search.** Theta\*-type planners remove grid artefacts by connecting a node directly to its grandparent when the line of sight is free, and Lazy Theta\* defers the visibility check to expansion time [nash2010lazy]. The acceptance rule relies on the triangle inequality of the Euclidean metric. SIPP [phillips2011sipp] compresses time into safe intervals per cell and adds wait actions. Any-angle SIPP [yakovlev2017aasipp] combines the two for agents moving among other agents. Both assume known obstacle trajectories and a time or length cost.

**Planning under uncertainty.** Chance-constrained path planning bounds the probability of collision [blackmore2011chance]. Du Toit and Burdick [dutoit2012robot] integrate prediction, estimation and planning in a receding-horizon scheme with chance constraints on uncertain agent locations. Zhu and Alonso-Mora [zhu2019chance] formulate chance-constrained nonlinear MPC for micro aerial vehicles among moving obstacles. These methods operate in continuous trajectory optimisation, typically over short horizons. Our work brings time-growing chance constraints into a graph search over a long horizon with an energy objective that includes wind.

**Rotorcraft power models.** We use the momentum-theory power model of [leishman2006principles] in the form adopted by Zeng et al. [zeng2019energy], extended with a climb term, and calibrate it on the flight-level records of [rodrigues2021dataset].

---

## 3. Problem Formulation

**Environment.** The airspace is a 3D grid with horizontal resolution $\Delta x=25$ m and vertical layers of $\Delta z=20$ m. The free set $\mathcal F$ contains cells between 30 and 150 m above ground level that are at least 20 m above buildings and outside static no-fly zones. The wind $\mathbf w(\mathbf x,t)$ is a 3D, time-varying field (Section 5.2).

**Decision variables.** A plan is $\pi=(\mathbf x_0,\dots,\mathbf x_N)$ with segment ground speeds $V_{g,i}$ and waiting times $\tau_i$. Segment $i$ has 3D length $d_i$, horizontal unit direction $\hat{\mathbf e}_i$ and slope $s_i$.

**Objective.** Minimise the predicted SOC consumption

$$
\Delta\mathrm{SOC}(\pi)=\frac{1+\epsilon_R}{E_b}\Big[\sum_{\ell}\sum_{i\in\ell}P\!\left(V_{a,i},V_{g,i}s_i,m_\ell,\rho(z_i)\right)\frac{d_i}{V_{g,i}\sqrt{1+s_i^2}}+\sum_i P_h\,\tau_i+E_{\rm TO}+E_{\rm LD}\Big]\times100 ,
$$

where $V_{a,i}=\lVert V_{g,i}\hat{\mathbf e}_i-\mathbf w(\mathbf x_i,t_i)\rVert$ is the airspeed, $m_\ell$ is the mass of leg $\ell$ (payload carried outbound for delivery, inbound for pickup), $E_b$ is the battery energy and $\epsilon_R$ an internal-resistance loss factor.

**Constraints.** The plan must satisfy the following.
- Airspeed and climb/descent limits: $V_{a,i}\le V_{a,\max}$ and $-V_{z,\rm dn}\le V_{g,i}s_i\le V_{z,\rm up}$.
- Time propagation: $t_{i+1}=t_i+\tau_i+d_i/(V_{g,i}\sqrt{1+s_i^2})$.
- Stay in free space: $\mathbf x(t)\in\mathcal F$.
- Energy reserve: $100-\Delta\mathrm{SOC}\ge \mathrm{SOC}_{\min}$.
- Per-instant chance constraints against each moving obstacle $j$:

$$
\Pr\big[\lVert \mathbf x_{xy}(t)-\mathbf p_j(t)\rVert\le R_j\big]\le\varepsilon\qquad\forall t .
$$

**Uncertainty model.** The planner receives a nominal prediction $\bar{\mathbf p}_j(t)$ issued at observation time $t_{{\rm obs},j}$, with isotropic Gaussian position error whose per-axis standard deviation is bounded by

$$
\sigma_j(t)=\sigma_{0,j}+\sigma_{v,j}\,(t-t_{{\rm obs},j})^+ ,
$$

and a Gaussian arrival-time error with standard deviation $\sigma_{t,j}$.

---

## 4. Method

### 4.1 Calibrated power model

The electrical power is

$$
P=\frac{k\,T\,v_i+P_0\big(1+3V_a^2/U_{\rm tip}^2\big)\rho/\rho_0+\tfrac12\rho\,C_DA\,V_a^3+P_{\rm clb}}{\eta}+P_{\rm av},
\qquad T=\sqrt{(mg)^2+(\tfrac12\rho C_DAV_a^2)^2},
$$

with induced velocity $v_i=\sqrt{\sqrt{V_a^4/4+v_h^4}-V_a^2/2}$ and $v_h^2=T/(2\rho A)$ [leishman2006principles, zeng2019energy]. The climb term is $P_{\rm clb}=mgV_z$ while climbing; 25 % of it is recovered while descending. We fix the mass at the specification-based value of 3.1 kg [확인요망: 실험 기체 질량 실측값] and fit $\eta$, $P_0$, $C_DA$ and $P_{\rm av}$ by nonlinear least squares on the flight-level records of [rodrigues2021dataset]. Training and test flights are split by date (Section 6.1).

### 4.2 Segment speed as an inner optimisation

For a segment direction $\hat{\mathbf e}$, slope $s$, wind $\mathbf w$ and mass $m$, the minimum energy per metre is

$$
e^\star(\hat{\mathbf e},\mathbf w,m)=\min_{V_g\in\mathcal V}\frac{P(\lVert V_g\hat{\mathbf e}-\mathbf w\rVert,V_gs,m)}{V_g\sqrt{1+s^2}},\qquad \mathcal V=\{3,3.5,\dots,20\}\ \mathrm{m\,s^{-1}} .
$$

An edge cost is $c(u,v,t)=\sum_k e^\star_k\,\Delta L_k$ over 12.5 m pieces, each evaluated with the wind at its position and passage time. A fixed-airspeed policy $V_a\equiv\bar V_a$ forces $V_g=w_\parallel+\sqrt{\bar V_a^2-w_\perp^2}$, which is one feasible point of the minimisation. Hence $e^\star\le e_{\rm fix}$ whenever that $V_g$ lies in $\mathcal V$.

### 4.3 Energy-compared any-angle shortcuts

Theta\* accepts the grandparent shortcut $p\!\to\!v$ whenever it is visible, which is justified by the triangle inequality of path length. Within one altitude layer and under uniform wind, the energy cost is $F(\mathbf d)=\lVert\mathbf d\rVert e^\star(\theta)$. This is an anisotropic norm, and it satisfies the triangle inequality only if its unit ball $\{F\le1\}$ is convex. For the calibrated drone and the forecast winds of the study day, the unit ball is **not** convex: the boundary lies 1.1–9.0 % inside its convex hull, depending on the layer (Section 6.2). A two-leg path can therefore be cheaper than the straight segment. WE-SIPP-Theta\* uses the rule

$$
\mathrm{par}(v)\leftarrow\arg\min\{\,g(u)+c(u,v,t_u),\;g(p)+c(p,v,t_p)\,\}
$$

and checks the shortcut for static and dynamic collisions while sampling it in time.

### 4.4 Closed-form safe intervals for time-growing uncertainty tubes

For an obstacle segment moving with velocity $\mathbf v$ from $\bar{\mathbf p}_a$ at time $t_a$, a grid cell centred at $\mathbf q$ is unsafe at $\tau=t-t_a\in[0,T]$ when

$$
\lVert \mathbf r-\mathbf v\tau\rVert\le\rho_0+\rho_1\tau,\qquad \mathbf r=\mathbf q-\bar{\mathbf p}_a,
$$

with $\rho_0=R+m_s+c+\kappa_\varepsilon\sigma(t_a)$ and $\rho_1=\kappa_\varepsilon\sigma_v$. Here $m_s$ is a fixed safety margin, $c=0.71\,\Delta x$ the cell half-diagonal, and $\kappa_\varepsilon=\sqrt{2\ln(1/\varepsilon)}$, so that $\Pr[\lVert\mathbf e\rVert>\kappa_\varepsilon\sigma]=\varepsilon$ for an isotropic bivariate Gaussian. Squaring both sides gives

$$
(\lVert\mathbf v\rVert^2-\rho_1^2)\,\tau^2-2(\mathbf r\cdot\mathbf v+\rho_0\rho_1)\,\tau+(\lVert\mathbf r\rVert^2-\rho_0^2)\le0 .
$$

When the obstacle moves faster than the tube grows ($\lVert\mathbf v\rVert>\rho_1$), the unsafe set is the interval between the roots. When the tube grows faster ($\lVert\mathbf v\rVert<\rho_1$), the leading coefficient is negative and the unsafe set is the **complement** of the open root interval within $[0,T]$, which can be two disjoint intervals. A slow obstacle with large velocity uncertainty can thus pass a cell and later re-cover it. Box-shaped obstacles reduce to four linear inequalities. Arrival-time uncertainty dilates each interval by $z_{1-\varepsilon/2}\sigma_t$.

The merged complements are the cell's safe intervals, so SIPP uses them unchanged. Because $\sqrt{\sigma_0^2+\sigma_v^2\tau^2}\le\sigma_0+\sigma_v\tau$, the tube is conservative at every instant for the generative model of Section 5.4. The constraint is per instant; it does not bound the probability of a collision anywhere along the trajectory. We measure that probability empirically (Section 6.4).

### 4.5 Search and replanning

The search state is (cell, safe interval). Successors are generated in three ways:
- departing immediately;
- waiting in the current safe interval when the edge is blocked;
- timing the departure so that the arrival falls in a neighbour's next safe interval.

Hover energy $P_h(m,\mathbf w)\tau$ is charged for waiting. The heuristic is $h=D_h\cdot\min_\theta e_{\min}(\theta)/\cos(\theta-\varphi)$. Here $e_{\min}(\theta)$ is the minimum energy per metre in heading $\theta$ over sampled wind vectors, scaled by 0.97, which makes the heuristic approximately admissible. Nodes are pruned when $g+h$ exceeds the remaining battery budget.

During flight, the obstacles are re-observed every $\Delta_{\rm obs}$ seconds, or whenever a new prediction is announced. Each observation resets the tube ($t_{\rm obs}\leftarrow t$). The remaining plan is checked against the new interval table, which is a table look-up, and replanning is triggered only on a predicted conflict. A computation latency of 3 s is simulated by continuing on the old plan before switching.

---

## 5. Experimental Setup

### 5.1 Site and mission
The site is a 6.2 × 5.5 km area of Seongnam (Pangyo–Bundang), Republic of Korea. Ground elevation is 17–291 m, and there are 7,109 buildings. Terrain comes from Copernicus GLO-30 DSM and building footprints and heights from Overture Maps (release 2026-10-21). The mission is a 2.4 km one-way leg from a hub to a delivery point with a 0.5 kg payload. The illustrative static no-fly zone is hypothetical. [확인요망: 실제 운항 시 서울공항 관제권 승인 필요 – 논문에 명시]

### 5.2 Wind
Wind comes from NOAA GFS 0.25° forecasts for 2026-10-27, a high-wind day with 100 m winds of 9–12 m s⁻¹ from the north-west. Ten heights are used (10–100 m above ground, 975 and 950 hPa), with hourly steps. The forecast is interpolated bilinearly in space, log-linearly in height and linearly in time. A simple terrain speed-up and urban-canopy factor in the range 0.80–1.19 is applied. Plans use the 00 UTC cycle. The 06 UTC cycle serves as the "actual" wind when evaluating execution. [주장약화: 06Z 갱신 예보는 관측이 아님 – 관측 바람(라이다/AWS)으로 대체 필요]

### 5.3 Drone and energy model
The drone is a DJI Matrice 100 with the calibrated power model of Section 4.1. The battery energy is 99.9 Wh [확인요망: 배터리 사양], $V_{a,\max}=17$ m s⁻¹, the climb rate is 3 m s⁻¹, the descent rate 2 m s⁻¹, and the SOC reserve is 20 %.

### 5.4 Uncertain-obstacle scenarios (Monte Carlo)
Thirty scenarios are drawn with a fixed seed. Each departs at a random hour between 15:00 and 18:00 local time and contains three cylindrical obstacles of radius 40–150 m covering all altitudes. Each obstacle crosses the obstacle-free nominal path at a random time with a random heading and a speed of 8–30 m s⁻¹. The prediction uncertainty is $\sigma_0=10$ m, $\sigma_v=0.5$ m s⁻¹ and $\sigma_t=2$ s, and the sensitivity analysis uses $\sigma_v\in\{0.25,1.0\}$. True trajectories follow

$$
\mathbf p^{\rm true}(t)=\bar{\mathbf p}(t-\delta t)+\mathbf e+\delta\mathbf v\,(t-t_{\rm obs}),
$$

with $\mathbf e\sim\mathcal N(0,\sigma_0^2I)$, $\delta\mathbf v\sim\mathcal N(0,\sigma_v^2I)$ and $\delta t\sim\mathcal N(0,\sigma_t^2)$. A collision is counted when the drone comes within the physical radius plus 0.5 m, without the safety margin.

The open-loop planners are:
- **N**: nominal prediction with a 15 m margin;
- **B**: constant buffer equal to the tube at 1.3× the nominal mission time, at $\varepsilon=0.05$;
- **R(ε)**: time-growing tube, $\varepsilon\in\{0.2,0.1,0.05,0.01\}$ (extended to $\{0.002,0.001,10^{-4}\}$ for the mismatch study);
- **B(ε)** and **M(ε)** (mismatch study only): the constant buffer at $\varepsilon\in\{0.2,0.1,0.05,0.01\}$ and the blended profile $\sigma_\lambda(\tau)=(1-\lambda)\sigma(\tau)+\lambda\sigma(T_h)$, $\lambda=0.5$, at $\varepsilon\in\{0.2,0.05,0.01,0.001\}$.

Each open-loop plan is evaluated against 1,000 sampled true trajectories, executed in the planning wind (primary) and in the updated wind (secondary). For the mismatch study, each plan is additionally evaluated against 1,000 trajectories whose $\mathbf e$, $\delta\mathbf v$ and $\delta t$ follow a Student-t distribution with 3 degrees of freedom, scaled to the same standard deviations. The closed-loop planners **N+O** and **R(0.05)+O** re-observe every 20 s. Each is evaluated on the first 20 scenarios × 30 sampled true trajectories. All searches use heuristic weight 1.0, except closed-loop replans, which use 1.2. [확인요망: 하드웨어 – 4-core cloud VM, numba JIT]

**Metrics.** We report:
- the collision probability, pooled over scenarios, with a Wilson 95 % interval;
- the per-scenario energy overhead relative to N, with a bootstrap 95 % interval of the mean (10,000 resamples);
- a paired Wilcoxon signed-rank test between R(ε) and B;
- the computation time.

---

## 6. Results

### 6.1 Energy model validation (RQ: is the cost model credible?)
The calibrated model was fitted on 142 flights from 12 dates and tested on 56 flights from 6 held-out dates. On the held-out dates it predicts flight energy with R² = 0.77 and MAPE = 9.6 % (Table 1). A distance-proportional model, which is the implicit cost of shortest-path planning, reaches R² = −1.47 and MAPE = 26.3 %. A duration-proportional model reaches R² = 0.68 and MAPE = 10.9 %. Rescaling the model with the first two flights of each test day lowers the MAPE to 8.6 %. The model under-predicts the mean power on the test dates by 3–9 % depending on the payload class [확인요망: 그림 F3 중간 패널 수치 – 389/359, 429/391, 441/426 W]. We attribute this to day-to-day conditions that the flight-level data do not record [주장약화: 기온·배터리 상태 미기록으로 원인 검증 불가].

**Table 1.** Energy model validation on held-out dates (56 flights).

| Model | R² | MAPE | RMSE |
|---|---|---|---|
| Distance-proportional | −1.47 | 26.3 % | 8.25 Wh |
| Duration-proportional | 0.68 | 10.9 % | 2.95 Wh |
| Calibrated rotorcraft model (ours) | **0.77** | **9.6 %** | 2.54 Wh |
| + daily rescaling (2 flights) | 0.79 | 8.6 % | 2.57 Wh |

### 6.2 Which planning components save energy? (RQ: where does the SOC saving come from?)
All runs in this section use a 15:00 departure on the high-wind day with a 0.5 kg payload. The baseline (shortest-length Lazy Theta\* at a constant 10 m s⁻¹) consumes 102.9 %p SOC for a delivery round trip, which exhausts the battery. WE-LT\* consumes 76.7 %p (delivery) and 78.5 %p (pickup), so both satisfy the 20 % reserve (Fig. F5).

Removing one component at a time (Table 2) shows the following:
- **Fixing the airspeed** costs the most: +10.2 to +13.0 %p at 14 m s⁻¹. At 10 m s⁻¹ no feasible plan exists, because the headwind exceeds the airspeed.
- **Restricting the search to the grid** (no energy-compared shortcuts) costs +4.1 %p and raises the delivery round-trip waypoint count from 37 to 199.
- **Removing altitude choice** costs +2.8 to +3.1 %p.
- **Freezing the wind at departure** or **ignoring the payload change** changes the round-trip SOC by at most 0.3 %p within a single ~10-minute flight.

For the last point, time variation matters through the choice of departure time instead: the delivery SOC ranged from 65.7 %p to 78.3 %p over departures between 09:00 and 20:00. In uniform-per-layer wind, the energy-cost unit ball is non-convex, lying 1.1–9.0 % inside its convex hull depending on the layer (Fig. F13b). Straight shortcuts are therefore not always cheaper, and the energy comparison of Section 4.3 is needed.

**Table 2.** Ablation: increase in round-trip SOC consumption relative to the full planner (15:00, 0.5 kg, evaluated in the updated wind).

| Removed component | Delivery | Pickup |
|---|---|---|
| Segment-speed optimisation (fixed V_a = 14 m s⁻¹) | +10.2 %p | +13.0 %p |
| Segment-speed optimisation (fixed V_a = 10 m s⁻¹) | infeasible | infeasible |
| Energy-compared any-angle shortcuts (grid search) | +4.1 %p | +4.2 %p |
| Altitude choice (terrain-following 2.5D) | +3.1 %p | +2.8 %p |
| Time-varying wind (frozen at departure) | +0.3 %p | +0.0 %p |
| Leg-specific mass (same mass both legs) | +0.2 %p | +0.1 %p |

### 6.3 Known moving obstacles
In the scenario of Fig. F14, three moving obstacles (another drone, an emergency helicopter and a temporary restriction) cross the obstacle-free route. The plan that ignores them intersects all three, with a worst penetration of 136 m. WE-SIPP-Theta\* keeps a minimum clearance of 29 m at +9.5 % energy and takes 2.8 s to plan. Waiting was not selected in this scenario: for this airframe the minimum cruise power (≈310 W) is below the hover power (≈400 W), so a 20–30 s delay is cheaper to absorb by a slightly longer path than by hovering.

### 6.4 Uncertain obstacle trajectories, open loop (RQ: does the time-growing tube buy safety more cheaply than a constant buffer?)
Table 3 and Fig. F16a summarise 30 scenarios × 1,000 sampled true trajectories. The prediction uncertainty is σ₀ = 10 m, σ_v = 0.5 m s⁻¹ and σ_t = 2 s.

- **Nominal planning (N)** collides in 16.4 % of runs (95 % CI 16.0–16.9 %). Collisions occur in 28 of the 30 scenarios.
- **The constant buffer (B)** and **the time-growing tube at ε = 0.05 and 0.01** produced no collision in 30,000 runs, so the upper 95 % bound is 0.013 %.
- **The tube at ε = 0.05** needs +11.5 % energy over N (95 % CI 7.7–16.3 %). B needs +17.1 % (13.6–21.1 %).
- **In the paired comparison**, the tube at ε = 0.05 uses 4.9 % less energy than B (95 % CI 3.7–6.0 %). It is cheaper in 28 of 30 scenarios (Wilcoxon signed-rank p = 1.6 × 10⁻⁷, Fig. F16b). At ε = 0.01 the saving is 2.2 % (0.9–3.5 %, 25 of 30 scenarios, p = 0.005).
- **The ε–risk relation is conservative.** The empirical collision rates are far below ε: 0.11 % at ε = 0.2 and 0.023 % at ε = 0.1. This is expected, because the tube uses the linear upper bound σ₀+σ_v τ, adds the 15 m margin and the cell half-diagonal, and ε bounds a per-instant probability.
- **Executing the same plans in the updated wind** gives practically the same collision rates and overheads (Table 3, last column). Within this experiment, the drone's own timing deviation is therefore not a significant risk source.

**Table 3.** Open-loop results (30 scenarios × 1,000 samples; σ_v = 0.5 m s⁻¹).

| Planner | Collision rate (95 % CI) | Scenarios with ≥1 collision | Energy overhead vs N (95 % CI) | Collision rate, updated wind |
|---|---|---|---|---|
| N (nominal) | 16.4 % (16.0–16.9) | 28/30 | – | 16.4 % |
| B (constant buffer) | 0 (≤0.013 %) | 0/30 | +17.1 % (13.6–21.1) | 0 |
| R (ε = 0.2) | 0.11 % (0.08–0.15) | 14/30 | +8.0 % (5.0–12.1) | 0.11 % |
| R (ε = 0.1) | 0.023 % (0.011–0.048) | 5/30 | +9.5 % (6.3–13.9) | 0.023 % |
| **R (ε = 0.05)** | **0 (≤0.013 %)** | 0/30 | **+11.5 % (7.7–16.3)** | 0 |
| R (ε = 0.01) | 0 (≤0.013 %) | 0/30 | +14.6 % (10.4–19.7) | 0 |

**How the robust plans buy safety.** The robust plans use time as well as space. Mean hover time per flight is 0.6 s for N, 7.5 s for the tube at ε = 0.05 (bootstrap 95 % CI 3.5–12.9 s), 9.7 s at ε = 0.01 and 10.5 s for B. B also delays take-off by 1.0 s on average (0–3 s), while the tube variants never do. Mean flight time rises from 132 s (N) to 145 s (tube, ε = 0.05) and 152 s (B). A plan must therefore be executed with its waits: re-flying the same paths without the planned waits changes the encounter timing by 10–12 s and invalidates the risk evaluation.

**Model mismatch: heavy-tailed prediction errors (Fig. F19, Table 3b).** All results above use errors drawn from the same Gaussian family that the planner assumes. To test robustness to this assumption, we re-evaluated every plan against Student-t errors with 3 degrees of freedom and the same standard deviations (seeds 30,000 + scenario id). We also added tube plans at ε ∈ {0.002, 0.001, 10⁻⁴}, constant buffers at ε ∈ {0.2, 0.1, 0.01}, and a blended profile σ_λ(τ) = (1−λ)σ(τ) + λσ(T_h) with λ = 0.5. The blend stays linear in τ, so its safe intervals keep the closed form of Section 4.4. Table 3b reports the 29 scenarios in which every variant found a plan.

- **Under Gaussian errors**, the tube family lies below the buffer family: at about +12 % energy the tube (ε = 0.05) had no collision in 29,000 runs, whereas the buffer (ε = 0.2) collided in 0.028 % of runs.
- **Under heavy-tailed errors, this ordering disappears.** At matched energy, the buffer has the lower risk: the tube at ε = 0.01 uses 2.2 % less energy than B (ε = 0.05) (95 % CI 0.9–3.6 %, p = 0.006) but collides in 0.28 % vs 0.18 % of runs. At matched risk, the energy difference is not significant: the tube at ε = 0.001 has risk 0.17 % vs 0.18 % and uses 1.2 % more energy (95 % CI −0.5–3.1 %, p = 0.27). At the lowest risk levels the buffer is better: B at ε = 0.01 reaches 0.069 % at +22.2 % energy, while the tube at ε = 10⁻⁴ reaches 0.12 % at +22.7 %.
- **The blended profile lies between the two families.** It does not dominate the constant buffer under heavy tails.
- **The nominal planner collides in 12.3 % (Student-t) vs 17.0 % (Gaussian) of runs on this subset.** Heavy-tailed errors with the same variance put more mass near zero, so small deviations are more frequent and large ones rarer at moderate radii.

The energy advantage of the time-growing tube is therefore conditional on the error model: it holds when the prediction error is close to Gaussian with a linearly growing scale, and it is lost under heavy-tailed errors. We return to the reason in Section 7.

**Table 3b.** Risk–energy trade-off under matched (Gaussian) and mismatched (Student-t, ν = 3) prediction errors (29 scenarios × 1,000 samples; energy overhead vs N, mean with bootstrap 95 % CI; collision rate with Wilson 95 % CI).

| Profile | ε | Energy overhead | Gaussian errors | Student-t errors |
|---|---|---|---|---|
| Tube (λ = 0) | 0.2 | +8.3 % (5.2–12.5) | 0.11 % (0.08–0.16) | 0.94 % (0.83–1.06) |
| Tube | 0.05 | +11.9 % (8.1–16.8) | 0 (≤0.013 %) | 0.53 % (0.45–0.62) |
| Tube | 0.01 | +15.1 % (10.9–20.4) | 0 | 0.28 % (0.22–0.35) |
| Tube | 0.001 | +19.3 % (14.4–25.3) | 0 | 0.17 % (0.12–0.22) |
| Tube | 10⁻⁴ | +22.7 % (17.6–28.7) | 0 | 0.12 % (0.08–0.16) |
| Blend (λ = 0.5) | 0.2 | +10.3 % (7.4–14.2) | 0.045 % | 0.58 % (0.50–0.67) |
| Blend | 0.05 | +14.6 % (10.9–19.1) | 0 | 0.26 % (0.20–0.32) |
| Blend | 0.01 | +19.0 % (15.2–23.6) | 0 | 0.15 % (0.11–0.20) |
| Blend | 0.001 | +24.1 % (19.9–29.1) | 0 | 0.086 % (0.06–0.13) |
| Buffer (λ = 1) | 0.2 | +12.0 % (9.0–15.8) | 0.028 % | 0.40 % (0.33–0.48) |
| Buffer | 0.1 | +14.9 % (11.6–18.8) | 0.003 % | 0.25 % (0.20–0.31) |
| Buffer | 0.05 | +17.6 % (14.3–21.6) | 0 | 0.18 % (0.14–0.24) |
| Buffer | 0.01 | +22.2 % (18.7–26.4) | 0 | 0.069 % (0.05–0.11) |


**Sensitivity to the uncertainty growth rate (Fig. F17).** The tube's energy advantage over B grows with σ_v:
- at σ_v = 0.25 m s⁻¹: 2.5 % (95 % CI 1.7–3.4 %, 26 of 28 decided scenarios);
- at σ_v = 0.5 m s⁻¹: 4.9 %;
- at σ_v = 1.0 m s⁻¹: 7.2 % (2.8–11.7 %, 22 of 29).

At σ_v = 1.0 m s⁻¹ both B and the tube produced a small number of collisions (0.038 % and 0.077 %). The nominal planner collides in 8.4 %, 16.4 % and 29.1 % of runs at the three growth rates.

### 6.5 Closed loop with re-observation (RQ: does the tube still matter when obstacles are re-observed?)
With re-observation every 20 s and replanning only on a predicted conflict (20 scenarios × 30 runs each):
- **Collisions.** The nominal planner (N+O) collided in 2 of 600 runs (0.33 %, 95 % CI 0.09–1.21 %). The tube planner (R(0.05)+O) collided in 0 of 600 (≤0.64 %). This difference is not statistically significant at this sample size [주장약화: 안전성 우위 주장 불가 – 표본 확대 필요].
- **Plan stability.** The tube reduced replanning from 0.32 to 0.087 replans per flight (−73 %; Wilcoxon over scenarios p = 0.002), and the mean replanning computation per flight from 0.25 s to 0.13 s.
- **Energy.** The tube cost 2.6 % more energy (95 % CI 1.0–4.5 %, p = 0.0008).

With announced obstacle changes instead of periodic observation (E9, Fig. F15), event-triggered checking matched the safety of 30 s periodic replanning with 2 replans instead of 5. Periodic replanning every 60 s detected a route change too late and collided.

**Table 4.** Closed-loop results (20 s re-observation, 3 s latency).

| Planner | Collisions | Replans per flight | Energy per flight (incl. take-off/landing) |
|---|---|---|---|
| N+O | 2/600 (0.33 %, CI 0.09–1.21 %) | 0.32 | 73.8 kJ |
| R(0.05)+O | 0/600 (CI ≤0.64 %) | 0.087 | 75.8 kJ (+2.6 %, CI 1.0–4.5) |

### 6.6 Computation
With heuristic weight 1.0 on a 4-core cloud VM [확인요망: CPU 모델], one leg takes 2.2 s to plan for N and 2.9–3.4 s for the tube variants. Building the unsafe-interval table for three obstacles takes about 0.1 s. Checking a remaining plan against a new table takes 50–300 ms. With weight 1.2, the platform replans a leg in about 2 s.


---

## 7. Discussion

**Why the tube is cheaper than a constant buffer.** A constant buffer must be sized for the largest look-ahead it may face. It therefore over-inflates obstacles at encounters that happen early in the flight, when the prediction is still accurate. The time-growing tube spends margin only where the prediction has degraded. Most of the paired saving comes from scenarios in which an obstacle is met in the first half of the mission [자료필요: 조우 시각별 절감 분해 분석 – 미실시]. The advantage grows with σ_v (Fig. F17), consistent with this explanation.

**Why the advantage depends on the error tail.** A first-order allocation argument explains the direction of the mismatch result. Consider n encounters with prediction scales σ_i and inflation radii r_i, and approximate the energy cost by a function increasing in Σ r_i. Minimising the summed risk Σ F̄(r_i/σ_i) subject to Σ r_i = R gives the stationarity condition f(r_i/σ_i)/σ_i = μ, where F̄ and f are the tail and the density of the normalised error radius.
- For the 2-D Gaussian (Rayleigh) radius, f(u) = u e^{−u²/2}, the optimal normalised radius u_i decreases only logarithmically with σ_i. Hence r_i ≈ κσ_i with an almost constant κ, which is the time-growing tube.
- For a power-law tail, F̄(u) ∝ u^{−α}, the condition gives u_i ∝ σ_i^{−1/(α+1)} and therefore r_i ∝ σ_i^{α/(α+1)}. For α = 3 (the Student-t case) this is r ∝ σ^{3/4}: the optimal inflation grows sublinearly with the prediction scale, so the profile is flatter than the tube and closer to a constant buffer.

This argument [주장약화: 비용을 Σr_i 로 근사한 1차 분석 – 실제 에너지 비용은 경로 기하에 따라 달라짐] predicts the direction of the change but not its size. In our experiment the blended profile did not outperform the buffer under heavy tails, so the argument should not be read as a prescription for λ. The practical consequence is that the *shape* of the inflation profile, not only ε, must be chosen from the empirical tail of the prediction errors. Profiles that are piecewise linear in τ keep the closed-form safe intervals of Section 4.4, so tail-adaptive profiles fitted to real track data are a direct extension.

**What ε means in this setting.** ε bounds the collision probability per instant and per obstacle under a linear upper bound on the prediction error. The empirical trajectory-level rates were 1–3 orders of magnitude below ε. Practitioners should therefore treat ε as a conservative tuning parameter rather than a calibrated mission risk. Calibrating ε to a target trajectory-level risk — for example with union bounds over encounter windows, or with scenario-based validation — is left for future work.

**Closed loop.** Frequent re-observation removes most of the risk of nominal planning, but at the price of more frequent plan changes. The tube keeps the plan valid for longer and costs a few percent more energy. Whether fewer plan changes matter operationally — for UTM negotiation and predictability for other airspace users — is outside the scope of our simulation. [인용필요: 계획 변경 빈도가 UTM/공역 운영에 미치는 영향에 대한 문헌]

**Limitations.**
1. **Single site, day and airframe.** All results come from one 6 × 5.5 km site, one high-wind day and one small quadrotor (DJI M100, 99.9 Wh). Generality to other cities, seasons and larger delivery drones is untested.
2. **Wind data.** GFS (0.25°) is coarse, and the terrain and urban corrections are heuristic. The "actual" wind is a later forecast cycle, not an observation.
3. **Synthetic obstacle uncertainty.** Obstacles and their uncertainty are generated, not observed. The main comparison uses a Gaussian generative model from the same family as the planner's assumption. Under heavy-tailed (Student-t) errors, the tube's energy advantage disappeared (Section 6.4). The practical value of the tube therefore depends on how close real prediction errors are to the assumed model, and this was not measured, because no real track data of aircraft or drones were used.
4. **Approximate optimality.** The search keeps one label per (cell, safe interval) and uses an approximately admissible heuristic, so it is not guaranteed optimal under time-varying costs. Small non-monotonicities between nested constraint sets can occur.
5. **No flight test.** The calibrated model was validated against recorded flights, but no planned trajectory was flown.


---

## 8. Conclusion

We presented a planner for delivery drones that minimises predicted SOC consumption in a time-varying 3D wind field. It jointly selects route, altitude, segment speed and waiting, and bounds the per-instant collision probability against moving obstacles with uncertain predictions.

On real terrain, buildings and forecast winds, with a power model validated on recorded flights:
- segment-speed optimisation and energy-compared any-angle shortcuts were the two largest sources of SOC saving;
- under Gaussian prediction errors, time-growing chance-constrained tubes, computed in closed form as SIPP safe intervals, matched the empirical safety of a constant worst-case buffer while using 4.9 % less energy (30 scenarios, p = 1.6 × 10⁻⁷);
- under heavy-tailed (Student-t, ν = 3) errors this advantage disappeared. At matched risk the energy difference was not significant, and at the lowest risk levels the constant buffer was better. The inflation profile must therefore match the tail of the error distribution;
- in closed loop with re-observation, the tubes reduced replanning by 73 % at a 2.6 % energy cost.

Future work covers real obstacle track data to identify the error tail and fit tail-adaptive (piecewise-linear) inflation profiles, calibration of ε to trajectory-level risk, multi-day and multi-site evaluation, observation-based wind, and flight tests.


---

## Data and Code Availability
The code, scenario seeds and processed data are available at [자료필요: 공개 저장소 URL/DOI – 현재 비공개 GitHub]. The raw data sources are Copernicus GLO-30 DSM, Overture Maps (ODbL), NOAA GFS (AWS Open Data) and the CMU Matrice 100 dataset (CC BY 4.0) [rodrigues2021dataset].

## References
See `references.bib`. Keys in square brackets refer to its entries.
