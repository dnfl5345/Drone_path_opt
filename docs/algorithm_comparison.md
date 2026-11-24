# WE-LT*와 최신 경로 알고리즘의 정식화 비교

이 문서는 제안 알고리즘 **WE-LT\* (Wind-aware Energy Lazy Theta\*)**의 최적화 문제를 수식으로 정의하고, 같은 문제를 다룬 최근 논문들의 정식화와 **무엇이 다른지**를 수식 단위로 비교합니다.
마지막 절에서는 각 논문의 가정을 우리 코드에서 하나씩 재현하는 **가정 축소 실험(E6)**으로 그 차이를 SOC 단위로 정량화했습니다.

> 문헌 [4]~[7]의 정식화는 공개된 초록과 서지 정보를 기준으로 정리했습니다. 원문에서 확인하지 못한 항목은 표에 **“미기재/미확인”**으로 표시했고, 추정해서 채우지 않았습니다(개발 환경에서 mdpi.com, sciencedirect, arxiv.org 직접 접속이 차단됨).

---

## 0. 참고문헌

| 번호 | 서지 정보 |
|---|---|
| [1] | A. Nash, S. Koenig, C. Tovey, “Lazy Theta\*: Any-Angle Path Planning and Path Length Analysis in 3D,” *Proc. AAAI Conference on Artificial Intelligence*, 2010. |
| [2] | M. Phillips, M. Likhachev, “SIPP: Safe Interval Path Planning for Dynamic Environments,” *Proc. IEEE ICRA*, 2011. |
| [3] | Y. Zeng, J. Xu, R. Zhang, “Energy Minimization for Wireless Communication With Rotary-Wing UAV,” *IEEE Trans. Wireless Communications*, 2019. DOI: 10.1109/TWC.2019.2902559 (arXiv:1804.02238). |
| [4] | F. Betti Sorbelli, F. Corò, S. K. Das, C. M. Pinotti, “Energy-Constrained Delivery of Goods With Drones Under Varying Wind Conditions,” *IEEE Trans. Intelligent Transportation Systems*, vol. 22, no. 9, pp. 6048–6060, 2021. DOI: 10.1109/TITS.2020.3044420 (arXiv:2012.08602). |
| [5] | F. Lian, B. Li, Q. Yang, H. Zhu, D. Du, “WA-LPA\*: An Energy-Aware Path-Planning Algorithm for UAVs in Dynamic Wind Environments,” *Drones*, vol. 9, no. 12, 850, 2025. DOI: 10.3390/drones9120850. |
| [6] | F. Lian, Q. Yang, B. Li, P. Wang, H. Luo, D. Du, “Optimal Path Planning of Unmanned Aerial Vehicle with Wind Field,” *Proc. 28th IEEE Intelligent Transportation Systems Conference (ITSC)*, Gold Coast, pp. 4195–4200, 2025. |
| [7] | R. Gu, Y. Zhao, X. Ren, “Integrating wind field analysis in UAV path planning: Enhancing safety and energy efficiency for urban logistics,” *Chinese Journal of Aeronautics*, vol. 39, no. 1, 103605, 2026. DOI: 10.1016/j.cja.2025.103605. |
| [8] | T. A. Rodrigues et al., “In-flight positional and energy use data set of a DJI Matrice 100 quadcopter for small package delivery,” *Scientific Data*, vol. 8, 155, 2021. DOI: 10.1038/s41597-021-00930-x. |
| [9] | J. G. Leishman, *Principles of Helicopter Aerodynamics*, 2nd ed., Cambridge University Press, 2006. |
| [10] | S. Koenig, M. Likhachev, D. Furcy, “Lifelong Planning A\*,” *Artificial Intelligence*, vol. 155, 2004. (WA-LPA\* [5]의 기반 알고리즘) |

※ 1차 보고서에 있던 “Highways in the Sky (TRB)”는 저자와 DOI를 확인할 수 없어 참고문헌에서 뺐습니다.

---

## 1. 공통 표기

| 기호 | 의미 |
|---|---|
| $\mathcal F \subset \mathbb R^3$ | 비행 가능 공간 (지면+30~150 m, 건물 상단+20 m, 정적 비행금지구역 제외) |
| $\mathcal O_k,\ [a_k,b_k]$ | 시간제 비행금지구역 $k$와 활성 시간 (장애물 시간표) |
| $\mathbf w(\mathbf x,t)$ | 3D·시간가변 바람 벡터 (GFS 고도별·시간별 예보의 보간값) |
| $\pi=(\mathbf x_0,\dots,\mathbf x_N)$ | 웨이포인트 열, 구간 $i$의 길이 $d_i$, 단위방향 $\hat{\mathbf e}_i$, 경사 $s_i$ |
| $V_{g,i},\ V_{a,i}$ | 구간 $i$의 수평 대지속도, 대기속도 $V_{a,i}=\lVert V_{g,i}\hat{\mathbf e}_i-\mathbf w\rVert$ |
| $\tau_i$ | 웨이포인트 $i$에서의 호버링 대기 시간 |
| $m_\ell$ | 구간(leg) $\ell\in\{\text{out},\text{back}\}$의 총질량 = 기체 $m_0$ + 탑재중량(배송: 갈 때, 회수: 올 때) |
| $P(V_a,V_z,m,\rho)$ | 회전익 소비전력 [3][9] (유도+형상+유해+상승)/η + 전장 |
| $E_b$ | 배터리 기준 에너지 (99.9 Wh) |

---

## 2. 제안: WE-LT\*의 문제 정식화

**(P) 에너지 최적 경로 = 예상 SOC 감소량 최소화**

$$
\min_{\pi,\ \{V_{g,i}\},\ \{\tau_i\}}\ \Delta\mathrm{SOC}
=\frac{1+\epsilon_R}{E_b}\Bigg[\sum_{\ell}\Big(\sum_{i\in\ell}\underbrace{P\big(V_{a,i},\,V_{g,i}s_i,\,m_\ell,\,\rho(z_i)\big)\frac{d_i}{V_{g,i}\sqrt{1+s_i^2}}}_{\text{② 구간 에너지}}+\sum_{i\in\ell}P_h(m_\ell,\mathbf w)\,\tau_i\Big)+E_{\rm TO}+E_{\rm LD}\Bigg]\times100
$$

제약 조건:

$$
\begin{aligned}
&V_{a,i}=\big\lVert V_{g,i}\hat{\mathbf e}_i-\mathbf w(\mathbf x_i,t_i)\big\rVert\le V_{a,\max},\qquad -V_{z,\rm dn}\le V_{g,i}s_i\le V_{z,\rm up} &&\text{(대기속도·상승률 한계)}\\
&t_{i+1}=t_i+\tau_i+\frac{d_i}{V_{g,i}\sqrt{1+s_i^2}} &&\text{(① 시간 전개, }d_i\text{: 3D 구간 길이)}\\
&\mathbf x(t)\in\mathcal F,\quad \mathbf x(t)\notin\mathcal O_k\ \ \forall t\in[a_k,b_k] &&\text{(정적·시간제 비충돌)}\\
&100-\Delta\mathrm{SOC}\ \ge\ 20 &&\text{(⑤ 예비 SOC)}
\end{aligned}
$$

여기서 $\epsilon_R$은 배터리 내부저항(I²R) 손실 계수입니다.

### 2.1 내부 최적화: 구간 속도의 닫힌 해 (열거)

경로와 속도를 분리합니다. 방향 $\hat{\mathbf e}$, 바람 $\mathbf w$, 질량 $m$이 주어지면 **미터당 에너지**는 1차원 최소화로 정해집니다.

$$
e^\star(\hat{\mathbf e},\mathbf w,m)=\min_{V_g\in\mathcal V}\ \frac{P\big(\lVert V_g\hat{\mathbf e}-\mathbf w\rVert,\ V_g s,\ m\big)}{V_g\sqrt{1+s^2}},\qquad \mathcal V=\{3,3.5,\dots,20\}\ \text{m/s}
$$

따라서 그래프 간선 비용은 $c(u,v,t)=\sum_{\text{piece}} e^\star(\cdot)\,\Delta L$이 됩니다. 구간을 12.5 m 조각으로 나누고, 조각마다 그 위치·시각의 바람을 씁니다.

- **고정 대기속도** 정책($V_a\equiv \bar V_a$)에서는 바람 삼각형의 해 $V_g = w_\parallel+\sqrt{\bar V_a^2-w_\perp^2}$가 강제되므로 $e_{\rm fix}\ge e^\star$입니다($\bar V_a$에 대응하는 $V_g$가 $\mathcal V$ 안에 있으면 항상 성립). 또 $w_\perp>\bar V_a$이거나 $V_g\le0$이면 비행 자체가 불가능합니다.

### 2.2 에너지 비교형 Lazy Theta\* (any-angle)

Theta\*/Lazy Theta\* [1]은 **거리 비용의 삼각부등식** $c(p,v)\le c(p,u)+c(u,v)$를 근거로, 조부모 $p=\mathrm{par}(u)$에서 $v$로 가시선(LOS)이 확보되면 조부모 직결(경로 2)을 무조건 채택합니다.
그러나 바람장에서의 에너지 비용 $c_E$는 방향·위치에 따라 달라지므로 일반적으로

$$
c_E(p,v)\ \not\le\ c_E(p,u)+c_E(u,v)
$$

입니다(예: 직선이 역풍이 강한 상층·능선을 지나고, 꺾인 경로가 바람이 약한 계곡을 지나는 경우). 그래서 WE-LT\*는 두 후보의 **에너지를 비교**합니다.

$$
\mathrm{par}(v)\leftarrow\arg\min\Big\{\,g(u)+c_E(u,v,t_u),\ \ g(p)+c_E(p,v,t_p)\,\Big\}
$$

경로 2의 LOS(정적 장애물·시간제 금지구역 포함)는 Lazy Theta\*처럼 $v$를 확장할 때 지연 검사합니다. 검사에 실패하면 닫힌 이웃 중 $\min_n g(n)+c(n,v)$로 부모를 교체하는데, 이때 **대기 후보도 함께** 평가합니다(§2.3). 웨이포인트 간격은 $L_{\max}=400$ m로 제한합니다.

### 2.3 호버링 vs 우회 (SIPP형 대기 행동)

격자 간선 $(u,v)$가 시간제 금지구역 $k$에 막히면, $u$에서 $\tau=b_k+1-t_u$만큼 대기한 뒤 통과하는 후보를 함께 만듭니다.

$$
c_{\rm wait}(u,v)=P_h\big(m,\lVert\mathbf w(\mathbf x_u,t_u)\rVert\big)\,\tau + c_E(u,v,t_u+\tau)
$$

따라서 탐색은 다음 부등식을 **암묵적으로** 판정합니다.

$$
\text{호버링 선택}\iff P_h\,\tau+E(\pi_{\rm direct})\ <\ E(\pi_{\rm detour})
$$

SIPP [2]는 (셀, 안전 시간구간) 상태와 대기 행동으로 **시간·충돌**을 다룹니다. WE-LT\*는 대기 비용을 **바람 속 호버링 전력**으로 정의해, 에너지 단위로 우회와 직접 비교합니다.

### 2.4 시간가변 비용과 휴리스틱

- 바람은 $\mathbf w(\mathbf x,t)=(1-\lambda)\mathbf w_{h}(\mathbf x)+\lambda\mathbf w_{h+1}(\mathbf x)$로 1시간 예보 사이를 선형 보간하고, 간선을 **진입하는 시각** $t_u$로 비용을 평가합니다(시간의존 라벨 $t$를 노드에 저장).
- 휴리스틱은 $h(n)=e_{\rm lb}\,\lVert\mathbf x_n-\mathbf x_g\rVert$이고, 계수는 다음과 같습니다.
  $$e_{\rm lb}=\min_{V_g\in\mathcal V}\ \min_{0\le w\le W_{\max}} \frac{P(|V_g-w|,0,m,\rho_{\min})}{V_g}$$
  최대 풍속이 정확히 순풍으로 분다고 가정한 하한이므로 수평·상승 간선에서는 허용적(admissible)입니다. 다만 하강 간선은 위치에너지 일부를 회수($c_{\rm desc}=0.25$)하므로 하한을 밑돌 수 있습니다(ε-허용).
- 노드당 도착 시각 라벨을 하나만 두므로 시간가변 비용에서는 **근사 최적**입니다. 대기 행동이 있어 FIFO(늦게 출발해 일찍 도착하는 일 없음) 위반은 완화됩니다.

### 2.5 탑재중량이 바뀌는 왕복 (배송/회수)

갈 때와 올 때를 서로 다른 질량 $m_{\rm out},m_{\rm back}$으로 **따로** 풉니다. 같은 경로라고 가정하고 1차 근사하면 다음과 같습니다.

$$
E_{\rm pickup}-E_{\rm delivery}\ \approx\ \frac{\partial P}{\partial m}\,\Delta m\,\big(T_{\rm back}-T_{\rm out}\big),\qquad \frac{\partial P_{\rm ind}}{\partial m}\propto \tfrac32\,m^{1/2}g^{3/2}
$$

역풍 귀환이면 $T_{\rm back}>T_{\rm out}$이므로 **회수가 배송보다 비쌉니다**. 실험값은 배송 76.7 %p, 회수 78.5 %p입니다(E1).

### 2.6 롤링 호라이즌 재계획

시각 $t_k=t_0+k\Delta$ ($\Delta=60$ s) 또는 이벤트(돌발 금지구역 발표) 시각에 현재 위치 $\mathbf x(t_k)$에서 (P)를 다시 풉니다. 이때 기체가 추정한 바람으로 예보 편향을 보정합니다.

$$
\hat{\mathbf w}_k(\mathbf x,t)=\mathbf w_{\rm fcst}(\mathbf x,t)+\mathbf b_k,\qquad
\mathbf b_k=0.4\,\mathbf b_{k-1}+0.6\big(\mathbf w_{\rm obs}(t_k)-\mathbf w_{\rm fcst}(\mathbf x(t_k),t_k)\big)
$$

---

## 3. 최신 연구의 정식화와 비교

### 3.1 문헌별 정식화 요약 (공개 초록 기준)

- **[1] Lazy Theta\*:** $\min_\pi \sum_i d_i$ (거리), any-angle, LOS 지연 검사. 바람·에너지·시간은 다루지 않습니다.
- **[2] SIPP:** 상태는 $(\text{cell},\ \text{safe interval})$이고 목적은 도착 시간 최소화입니다. 대기 행동으로 동적 장애물을 회피합니다.
- **[4] Betti Sorbelli 외:** 배송 그래프에서 간선 에너지 $e_{ij}(\text{payload},\ \text{wind})$가 바람(풍향 상대각)과 탑재중량에 따라 달라지고, 배터리 예산 $B$ 아래에서 임무 실행 가능성을 판정·최적화합니다. 바람이 바뀌면 비행 중 경로를 변경할 수 있습니다. 공간은 그래프(도로망형)이며, 연속 3D 공간 탐색과 고도는 다루지 않습니다.
- **[5] WA-LPA\*:** 격자 위에서 LPA\* [10]의 증분 재계획($g$, $rhs$ 일관성 유지)을 사용합니다. 비용은 유도·형상·유해 동력 기반 에너지 모델, 휴리스틱은 바람장 정렬(wind-field alignment)을 반영한 복합 휴리스틱이고, 계층적 고도 최적화(height-aware)를 합니다. 바람 변화 크기에 따라 전역 재계획과 국소 조정 중 하나를 고릅니다. **속도 최적화·탑재중량 변화·대기 행동: 미기재/미확인.**
- **[6] Wind-augmented A\* (ITSC 2025):** A\*에서 국소 바람으로 예측한 에너지 비용으로 휴리스틱을 동적으로 조정합니다. **고도·시간가변·중량: 미확인.**
- **[7] Wind-aware Theta\* (CJA 2026):** CFD로 만든 도시 **정상상태** 바람장 $\mathbf w(\mathbf x)$ 위에서 Theta\* 계열 탐색과 에너지 비용을 씁니다. 바람 미고려 대비 에너지 7.69 % 절감, 대지속도 6.23 % 향상을 보고했습니다(선전 사례). **속도 최적화·중량·시간가변: 미확인.**

### 3.2 차이점 정리 (수식의 구성요소 단위)

| 구성요소 | [1] Lazy Θ\* | [2] SIPP | [4] Betti Sorbelli | [5] WA-LPA\* | [6] Wind A\* | [7] Wind Θ\* | **WE-LT\* (제안)** |
|---|---|---|---|---|---|---|---|
| 목적함수 | 거리 | 시간 | 에너지(예산 내 임무) | 에너지 | 에너지 | 에너지 | **ΔSOC = ΣP·t/E_b (+I²R)** |
| 바람 $\mathbf w$ | 없음 | 없음 | 변동 바람 | 동적 바람장 | 국소 바람 | 정상상태 CFD $\mathbf w(\mathbf x)$ | **$\mathbf w(\mathbf x,t)$ 3D·시간보간 (실예보)** |
| 공간 | 3D 격자 any-angle | 격자 | 그래프 | 격자 + 고도 계층 | 격자 | any-angle | **3D 26방향 + any-angle** |
| 직결 채택 규칙 | LOS면 채택 | – | – | – | – | LOS면 채택(Theta\*) | **LOS ∧ 에너지 비교 (§2.2)** |
| 속도 결정 | – | – | 미확인 | 미확인 | 미확인 | 미확인 | **구간별 $e^\star$ 내부 최적화 (§2.1)** |
| 질량 | – | – | 탑재중량 반영 | 미확인 | 미확인 | 미확인 | **leg별 $m_\ell$ (배송/회수, §2.5)** |
| 대기(호버링) | – | 대기 행동 | – | 미확인 | – | – | **에너지 기준 대기 vs 우회 (§2.3)** |
| 재계획 | – | – | 비행 중 변경 | LPA\* 증분 | – | – | **롤링 호라이즌 + 바람 편향 보정 (§2.6)** |
| 실측 보정 | – | – | – | 미확인 | 미확인 | 미확인 | **DJI M100 198회로 계수 보정 [8]** |

요약하면, WE-LT\*는 기존 연구들이 **나눠서** 다룬 다섯 결정 축 (경로 any-angle, 고도, **속도**, **대기 시간**, **leg별 질량**)을 하나의 목적함수 ΔSOC 아래에서 **동시에** 결정합니다. 특히 다음 두 가지가 다릅니다.
1. 바람장에서 삼각부등식이 깨지는 점을 반영해 **에너지 비교형 지름길 규칙**을 쓴다는 점
2. 속도를 경로 탐색의 **내부 최적화**(간선 비용 정의)로 흡수한다는 점

반대로 [5]의 **증분(LPA\*) 재계획**은 재계산량 면에서 우리 롤링 재계획(매번 전체 재탐색, 평균 5.3 s)보다 효율적이므로, 향후 결합할 후보입니다.

---

## 4. 정량 비교: 가정 축소 실험 (E6)

§3.2의 각 **비교 축**을 하나씩 단순화한 변형을 우리 코드에서 만들고, 같은 지도·바람·기체·평가 기준으로 SOC 손실을 측정했습니다.
문헌이 해당 축을 어떻게 처리하는지 원문에서 확인하지 못한 경우(§3.2의 “미기재/미확인”)에는 그 문헌의 결과로 해석하지 않고, **그 축을 단순화했을 때의 손실**로만 해석합니다.
조건은 2026-10-27 15:00 출발, 0.5 kg, 실제 바람(06Z 갱신)으로 평가했습니다. 결과는 `results/summary.json` → `E6_ablation`에 있습니다.

| 변형 (재현한 가정) | 비교 축 | 배송 SOC [%p] | Δ | 회수 SOC [%p] | Δ | 웨이포인트(배송) |
|---|---|---|---|---|---|---|
| **제안 WE-LT* (전체)** | - | 76.7 | +0.0 | 78.5 | +0.0 | 37 |
| 시간불변 바람 (출발시각 스냅샷) | 시간가변성 축 | 77.0 | +0.3 | 78.5 | +0.0 | 38 |
| 격자 탐색 (any-angle 없음) | 탐색공간 축 ([5][6] A*/LPA* 계열) | 80.8 (예비 미달) | +4.1 | 82.7 (예비 미달) | +4.2 | 199 |
| 고정 대기속도 14 m/s (속도 최적화 없음) | 속도 결정 축 | 86.9 (예비 미달) | +10.2 | 91.6 (예비 미달) | +13.0 | 34 |
| 고정 대기속도 10 m/s | 속도 결정 축 | 계획 불가 | – | 계획 불가 | – | – |
| 탑재중량 변화 무시 (왕복 같은 질량으로 계획) | 질량 축 | 76.9 | +0.2 | 78.6 | +0.1 | 43 |
| 고도 선택 없음 (지형추종 2.5D) | 고도 축 | 79.8 | +3.1 | 81.3 (예비 미달) | +2.8 | 44 |
| 결합: 시간불변 바람 + 격자 + 중량 고정 | 3개 축 동시 축소 | 80.8 (예비 미달) | +4.1 | 82.7 (예비 미달) | +4.2 | 199 |

**해석**

| 축 | ΔSOC | 의미 |
|---|---|---|
| 구간 속도 최적화 (§2.1) | **+10.2 ~ +13.0 %p**, 10 m/s 고정이면 **비행 불가** | 가장 큰 차이입니다. 강풍에서 고정 대기속도는 순풍 구간에서 에너지를 낭비하고, 역풍 구간에서는 바람 삼각형의 해가 없어집니다. |
| 에너지 비교형 any-angle (§2.2) | **+4.1 %p**, 웨이포인트 37 → 199 | 격자 탐색은 계단형 경로 때문에 손실이 생기고, 예비 SOC 20 %를 지키지 못합니다. |
| 3D 고도 선택 | **+2.8 ~ +3.1 %p** | 역풍 구간에서 바람이 약한 저고도를 고르지 못합니다. |
| 시간가변 바람 (비행 1회 내) | +0.0 ~ +0.3 %p | 약 10분 비행 동안에는 예보가 크게 바뀌지 않아 효과가 작습니다. 시간가변성은 **출발시각 선택**(E2: 65.7 ~ 78.3 %p)과 **재계획**(E5)에서 효과가 납니다. |
| leg별 질량 (경로 선택) | +0.1 ~ +0.2 %p | 질량은 경로 **형상**보다 **SOC 예측값**에 영향을 줍니다(배송/회수 차이 1.8 %p, 0→750 g 회수에서 +7 %p). |


이 밖의 축은 별도 실험으로 측정했습니다.
- **대기 행동 없음 ([7]처럼 우회만):** 띠형 시간제 금지구역이 30 s 뒤 해제될 때 편도 에너지가 57.9 → 70.9 kJ로 +22 %입니다(E4).
- **재계획 없음:** 돌발 금지구역을 침범합니다(안전 위반). 재계획하면 SOC +7.3 %p로 회피합니다(E5).
