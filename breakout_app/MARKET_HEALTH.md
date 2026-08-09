# Sức khỏe thị trường (Market Health) — tài liệu chi tiết

> Code: `engine/market_health.py` (thuần, không I/O) · gom dữ liệu: `scheduler._compute_market_health`
> · gating: `scheduler._mh_mode` / `_mh_pass` · knob: `MH_GATE_*` trong `config.py`
> · lịch sử quyết định: DEVELOPMENT.md #31 (phase 1), #39-40 (phase 2) · luật vận hành: Spec RevD §2.8

## 1. Triết lý — đo độ mong manh, không đoán thời điểm

Không mô hình nào dự đoán được *ngày* thị trường điều chỉnh (về nguyên lý — nếu đoán được thì
nó đã bị arbitrage mất). Market Health chỉ trả lời câu hỏi khiêm tốn hơn nhưng trả lời được:
**"nếu có cú sốc, thị trường hiện tại chịu đòn tốt hay gãy ngay?"** — tức đo *độ mong manh*
(fragility), giống đo độ khô của rừng thay vì đoán ngày cháy.

Nó bổ sung cho **regime gate** (close/MA20 của VN-Index): regime gate là phanh *phản ứng sau*
— chỉ chuyển `blocked` khi index đã gãy MA20, tức đã mất vài %. Market Health điều tiết
*trước* — hạ chuẩn khuyến nghị khi nền thị trường đã yếu dưới bề mặt dù index còn xanh.

## 2. Công thức tổng

```
health = 0.30·score_dist + 0.30·score_breadth + 0.20·score_canary + 0.20·score_index   (0..100)
```

| Health | Nhãn banner |
|---|---|
| ≥ 70 | 🟢 Khỏe |
| 50–69 | 🟡 Trung tính |
| 30–49 | 🟠 Yếu |
| < 30 | 🔴 Rất yếu |

Trọng số 0.30/0.30/0.20/0.20 là heuristic thiết kế (dist và breadth nặng hơn vì là hai tín
hiệu *sớm* nhất — xem §7 về mức độ kiểm chứng). Mỗi thành phần được chấm 0–100 theo bảng
bậc thang riêng rồi lấy trung bình có trọng số.

## 3. Bốn thành phần

### 3.1 `dist` — Phiên phân phối O'Neil (trọng số 0.30)

**Đo cái gì:** tổ chức lớn đang âm thầm xả hàng. Một **phiên phân phối** = VN-Index giảm
> 0.2% với **volume cao hơn phiên trước** (giá xuống trên khối lượng lớn = có người bán chủ
động khối lượng lớn). Đếm trong cửa sổ **25 phiên** gần nhất. Đây là công cụ nổi tiếng nhất
của William O'Neil để nhận diện đỉnh: thị trường không sập vì một tin xấu, nó sập sau 4–6
phiên phân phối tích tụ trong vài tuần — dấu vết của smart money rút dần.

**Luật hết hạn (Phase 2 — sửa lỗi quan trọng):** một phiên phân phối bị **xóa khỏi sổ đếm**
khi index sau đó từng đóng cửa cao hơn close của phiên đó **≥ 5%** (rally đã hấp thụ hết áp
lực bán — O'Neil nguyên bản). Thiếu luật này, bộ đếm bão hòa 6–7 ngay giữa uptrend tháng
6/2026 (lỗi phát hiện ở backtest phase 1): mọi nhịp rung lắc bình thường của một uptrend
khỏe đều bị tính là phân phối vĩnh viễn.

**Bảng điểm** (`_score_dist`):

| Số phiên phân phối còn hiệu lực | Điểm |
|---|---|
| 0–1 | 100 |
| 2 | 80 |
| 3 | 60 |
| 4 | 40 |
| 5 | 20 |
| ≥ 6 | 0 |

**Nguồn dữ liệu:** VN-Index OHLCV (cần cột volume) — từ 30/07 được refetch mỗi scan nên đếm
cả nến đang hình thành trong phiên.

### 3.2 `breadth` — Độ rộng thị trường (trọng số 0.30)

**Đo cái gì:** % mã trong pool Layer-1 đang đóng cửa **trên MA20 của chính nó**. Đây là máy
phát hiện **phân kỳ độ rộng** — kiểu đỉnh nguy hiểm nhất: index vẫn xanh (được kéo bởi vài
trụ) trong khi đa số cổ phiếu đã âm thầm gãy MA20. Index nói "ổn", breadth nói "bên dưới
đang rữa" — tin breadth.

**Cách tính:** với từng mã, MA20 lấy từ **20 phiên TRƯỚC** (không gồm giá hiện tại — tránh
giá hôm nay tự kéo trung bình của chính nó); giá so sánh = close live trong phiên. Cần ≥ 21
bars mới tính; mã thiếu lịch sử bị bỏ qua chứ không đoán.

**Bảng điểm** (`_score_breadth`):

| % mã > MA20 | Điểm | Đọc là |
|---|---|---|
| ≥ 70% | 100 | tăng đồng thuận |
| 55–69% | 80 | khỏe |
| 40–54% | 60 | trung tính |
| 25–39% | 40 | yếu rõ |
| < 25% | 20 | gãy diện rộng |
| Không tính được (NaN) | 70 | ⚠️ mặc định lạc quan — xem §7.1 |

### 3.3 `canary` — Chim hoàng yến leadership (trọng số 0.20)

**Đo cái gì:** trong các mã app **đã khuyến nghị 1–2 phiên gần nhất**, bao nhiêu % hiện còn
giữ giá **≥ close của ngày khuyến nghị**? Nguyên lý: cổ phiếu dẫn dắt (mã breakout mạnh
nhất) gãy **trước** chỉ số — breakout chết hàng loạt ngay T+1 là tín hiệu sớm nhất của dòng
tiền rút. Quan sát khai sinh ra thành phần này: tối 08/07/2026, cả lứa khuyến nghị 7/7 đỏ
toàn bộ trong khi VN-Index vẫn +0.29% — hai hôm sau thị trường điều chỉnh mạnh.

**Cách tính:** lấy các tín hiệu từ `db.recent_reco_entries` (2 phiên gần nhất), entry =
`db.close_on(sym, ngày_KN)` (cùng hệ điều chỉnh giá — chống lỗi corporate action), so với
close live. **Cần ≥ 3 tín hiệu** mới tính (ít hơn thì vô nghĩa thống kê → None).

**Bảng điểm** (`_score_canary`):

| % tín hiệu còn giữ giá | Điểm |
|---|---|
| ≥ 60% | 100 |
| 40–59% | 70 |
| 20–39% | 40 |
| < 20% | 10 |
| Không có lứa gần đây (None) | 70 — trung tính, xem §7.1 |

### 3.4 `index` — VN-Index / MA20 (trọng số 0.20)

**Đo cái gì:** chính đại lượng của regime gate, nhúng vào health với trọng số nhỏ để điểm
tổng phản ánh cả trạng thái xu hướng thô. MA20 = trung bình 20 phiên gần nhất (gồm phiên
hiện tại).

**Bảng điểm** (`_score_index`):

| close/MA20 | Điểm |
|---|---|
| ≥ 1.00 | 100 |
| 0.99–1.00 | 80 |
| 0.97–0.99 | 55 |
| < 0.97 | 20 |
| NaN | 70 |

Lưu ý ngưỡng ăn khớp với regime gate: `blocked` khi ratio < 0.97 **và** MA5 < MA20;
`caution` khi < 1.00.

## 4. Ví dụ tính tay

Phiên giả định: 3 phiên phân phối còn hiệu lực · 45% mã trên MA20 · lứa khuyến nghị gần
nhất còn 2/5 mã giữ giá (40%) · index/MA20 = 0.985.

```
score_dist    = 60    (3 phiên)
score_breadth = 60    (45%)
score_canary  = 70    (40%)
score_index   = 55    (0.985)

health = 0.30×60 + 0.30×60 + 0.20×70 + 0.20×55 = 18 + 18 + 14 + 11 = 61 → 🟡 Trung tính
```

## 5. Đèn vàng — health điều tiết khuyến nghị thế nào (Phase 2)

| Health | Chế độ (`_mh_mode`) | Hành vi |
|---|---|---|
| ≥ 55 | ✅ `normal` | như thiết kế (BUY ≥ 50) |
| 40–54 | ⚕️ `selective` | chỉ alert/ghi nhận mã **BUY ≥ 65**; alert kèm ghi chú ⚕️ |
| < 40 | ⛔ `halt` | không khuyến nghị mới |

`_mh_pass(mode, buy)` được dùng **nhất quán ở cả ba nơi**: alert Telegram, `tracked_signals`
(kiểm chứng T+n) và cờ `is_reco` của `daily_observations` — để dữ liệu học/kiểm chứng phản
ánh đúng những gì thực sự được khuyến nghị.

Knob trong `config.py`: `MH_GATE_ENABLED` (tắt toàn bộ trong 1 dòng), `MH_GATE_SOFT=55`,
`MH_GATE_HARD=40`, `MH_GATE_STRONG_SCORE=65`.

**Quan hệ với regime gate:** hai lớp độc lập, health chạy TRƯỚC vòng chấm điểm mỗi scan.
Regime `blocked` chặn tuyệt đối bất kể health; health `halt` chặn cả khi regime còn `ok` —
lớp nào "đỏ" trước thì lớp đó phanh.

## 6. Bằng chứng thực nghiệm

- **Phase 1 (quan sát, 23 phiên 6–7/2026):** điểm health sụp đúng nhịp qua đợt điều chỉnh
  tháng 7 (46→34→30→19→18), cảnh báo từ **09/07** — trước khi regime gate chuyển blocked.
- **Phase 2 (gating, kho backtest 10 năm, objective `retT5 − 0.3·|MAE5|`):** biến thể GRAD
  soft=55 — TRAIN −0.019→+0.018, VALIDATION −1.115→−1.009. Ngưỡng chọn trên TRAIN, xác nhận
  trên VALIDATION (dữ liệu chưa từng dùng chọn ngưỡng). Các biến thể HARD/LATEOFF và ngưỡng
  khác đều thua hoặc không cải thiện đồng thời hai giai đoạn → bị loại.

## 7. Ba điểm yếu đã ghi nhận (ranh giới sử dụng)

### 7.1 Lệch lên khi thiếu dữ liệu
`breadth` NaN, `canary` None, `index` NaN đều mặc định **70** — "không biết" bị chấm thành
"tạm ổn". Trường hợp xấu nhất (cả 3 thiếu, dist=0) đọc ra ~79 điểm dù máy đo chưa cắm điện.
Kiểu sai một chiều: không bao giờ báo động nhầm, chỉ có thể **ru ngủ nhầm**. Thực tế hay gặp
ngay sau restart app / đầu ngày khi lịch sử chưa nạp đủ → **đừng tin số đọc của scan đầu
tiên sau khởi động**.

### 7.2 Canary trễ ở đỉnh hưng phấn
Tại đỉnh, các mã breakout *vẫn đang chạy* (chính là biểu hiện của hưng phấn) → canary đọc
100. Nó chỉ tụt **sau** khi leadership bắt đầu gãy (trễ 1–3 phiên sau đỉnh). Backtest 7/2026:
01/07 health 70, 07/07 còn 58 — "ổn" — rồi thị trường rơi −3.8%. Canary là chỉ báo *xác nhận
sớm sự sụp đổ đã bắt đầu*, không phải máy gọi đỉnh; thành phần `dist` bù chỗ này (phân phối
tích tụ *trước* đỉnh).

### 7.3 Hiệu ứng gating dưới ngưỡng nhiễu ở TRAIN
Cải thiện trên TRAIN (+0.037) chỉ bằng ~1/3 sai số bootstrap của objective (σ=0.107) — một
mình nó thì không phân biệt được với 0 và đáng lẽ bị loại theo luật parsimony. Được chấp
nhận vì **VALIDATION cải thiện cùng chiều, rõ hơn, trên thị trường gấu** — đúng môi trường
một cơ chế phanh phải chứng minh giá trị. Hàm ý: gating được ship với mức tin cậy THẤP hơn
regime gate/kênh PRE; là ứng viên đầu tiên xem xét lại khi Drift Alarm có dữ liệu live, và
tắt được bằng `MH_GATE_ENABLED=False` nếu bằng chứng live không ủng hộ.

## 8. Vận hành & lưu trữ

- Tính **mỗi scan** (5 phút trong phiên) tại `_compute_market_health`, TRƯỚC vòng chấm điểm.
- Hiển thị: banner dashboard (điểm + nhãn + chế độ đèn vàng) · log scheduler.
- Lưu: `store.market_health` (trạng thái hiện tại) + bảng `market_health` trong
  `data/screener.db` (lịch sử theo ngày — mỗi ngày một dòng, scan sau ghi đè scan trước
  trong cùng ngày; dòng cuối ngày ≈ giá trị EOD).
- Backtest/kiểm chứng lại: `analysis/market_health_backtest.py` (phase 1, 23 phiên) và
  `analysis/mh_phase2.py` (dựng health 10 năm từ kho backtest + thử các biến thể gating).
