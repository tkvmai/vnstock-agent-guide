# Hồ sơ review các mã BỎ SÓT (missed-winner post-mortem)

> Đối tượng: mã **không được khuyến nghị** (is_reco=0) nhưng **thắng ≥ `MISS_MIN_RET_T3` (3%)**
> tại T+3 — false negative của screener. Nguồn: tab "📊 Bỏ sót — Toàn pool" /
> `db.unreviewed_misses()`. Sau khi review từng ca → `db.mark_miss_reviewed(sym, ngày, kết_luận)`.
>
> Quy trình: chart TradingView + dữ liệu vnstock → trả lời "vì sao bị loại, và việc loại đó
> ĐÚNG hay SAI?" Chỉ khi một lý do loại-sai LẶP LẠI (≥3 ca) mới đề xuất nới công thức, kiểm
> chứng trên `daily_observations` trước khi áp dụng — nới gate làm tăng số mã được khuyến nghị,
> phải chắc chắn win-rate nhóm thêm vào không kéo tụt tổng thể.
>
> **Phân loại kết luận:** `loại_hợp_lệ` (thắng nhờ may/tin tức — không sửa gì) ·
> `thrust_gate_quá_chặt` · `cửa_sổ_pre_quá_hẹp` (coil >3% dưới đỉnh) · `ngưỡng_điểm_cao` ·
> `muộn_nhưng_vẫn_chạy` (LATE mà thắng — cân nhắc lại state_mult 0.6) · `khác`

---

## Đợt review 19/07/2026 — 26 ca bỏ sót đầu tiên (quan sát 09-14/07)

**Kết luận tập thể: `loại_hợp_lệ` — cả 26 ca là một pattern duy nhất: đảo chiều đáy V,
ngoài phạm vi thiết kế của hệ breakout.**

- **Cấu trúc chung:** 100% ca là state NONE / BUY 0.0 — giữa sự kiện điều chỉnh 9-15/7 mọi
  mã đều sâu dưới pivot 20 phiên, cấu trúc MA gãy, RS âm → không thể là FRESH/PRE theo bất
  kỳ định nghĩa breakout nào (kể cả PRE đã nới 1.05). Người thắng lớn (HCM +8.6%, PVD +7.0%,
  ACB +5.8%…) là các mã bật mạnh nhất từ đáy 13/7 — nhóm chứng khoán/dầu khí beta cao.
- **Base rate phán quyết:** trong CÙNG cửa sổ, toàn pool trung bình ÂM (9/7: −1.75% · 10/7:
  −2.31% · 13/7: −0.16% · 14/7: −2.31%), chỉ 3-16% số mã đạt ≥3%. Tức "mua đáy" các ngày đó
  về kỳ vọng là THUA — các ca bỏ sót là đuôi phải may mắn, không phải tín hiệu bị lọc oan.
  Nhịp bật 16/7 còn TẮT ngay 17/7 (pool 14/7 → T+3 âm trở lại) — đúng kiểu bẫy hồi kỹ thuật.
- **Hệ thống đứng ngoài là ĐÚNG:** regime blocked + health 19-37 (⛔ halt) trong toàn bộ cửa
  sổ; với ràng buộc khóa T+2.5, bắt dao rơi ngày 13/7 rủi ro thêm một chân giảm là có thật
  (và đã xảy ra ngày 17/7).

**Khoảng trống chiến lược được ghi nhận (không phải lỗi):** hệ RevD không có và không định
có năng lực bắt đáy V — đó là một chiến lược khác (mean-reversion) với edge/rủi ro khác hẳn.
Nếu muốn, phải là quyết định sản phẩm mở kênh mới với spec riêng — số liệu base rate ở trên
hiện KHÔNG ủng hộ (kỳ vọng âm nếu vào bừa; cần tiêu chí chọn lọc riêng chưa được nghiên cứu).

---

## Đợt review 05/08/2026 — TOÀN BỘ cửa sổ trắng khuyến nghị 20-31/07 (33 ca, user chất vấn "có thật là downtrend?")

**Bối cảnh:** app không khuyến nghị mã nào suốt 10 phiên 20→31/07 (20-29/07 regime blocked;
30-31/07 regime đã caution nhưng MH halt health 25). Một user cho rằng thị trường "không phải
downtrend". Phương pháp: **replay engine nguyên bản trên cả 10 phiên, TẮT cả regime gate lẫn
MH gate** (xấp xỉ EOD như backtest; 1,133 mã-ngày / ~113 mã/phiên) + đo base rate toàn pool.

**1. Downtrend là có thật (theo thước đo chiến thuật):** VN-Index −11.2% từ đỉnh (1,878 →
1,669), phiên 22/07 −3.58%; breadth đáy 5-11% mã trên MA20; pool T+3 các ngày 20-23/07 âm
−1.6%…−2.7% với win 21-28%. Mua bất kỳ trong nửa đầu cửa sổ là thua về kỳ vọng. Điểm user
ĐÚNG: đây là điều chỉnh nhanh chữ V trong uptrend lớn (2 tuần sau index đã lại trên MA20) —
khác "bear market". Hai bên dùng hai khung thời gian khác nhau; gate của app là thước 20 phiên.

**2. Nếu KHÔNG có gate, app cũng gần như không có gì để khuyến nghị:** cả 10 phiên chỉ có
**12 tín-hiệu-ngày** FRESH/PRE đạt BUY≥50 (98% pool là NONE). 10 ca đã đủ T+3: **trung bình
−0.90%, win 4/10** — tức gate không hề "giam" một mỏ vàng; chính setup của hệ cũng thua trong
giai đoạn đó. Regime gate tiếp tục được xác nhận.

**3. "Bỏ sót" 99 mã ≥+3% nhưng bản chất là 3 phiên bật đáy:** 78/112 mã (70%) ngày 27/07 và
73/113 (65%) ngày 29/07 đạt ≥+3% T+3 — khi 2/3 pool cùng "thắng" thì đó là beta bật đáy V,
không phải chọn mã. Toàn bộ top winners (FRT +22.4% T+3, VCI +16.5%, VRE, VHM, MWG, GEX,
VCB…) đều **state NONE, BUY 0** — sâu dưới pivot, MA gãy — đúng pattern 26 ca đợt 19/07
(`loại_hợp_lệ`, bắt đáy V ngoài thiết kế). Cả kỳ 17/07→04/08 pool trung bình **−1.97%**, chỉ
38% mã ≥0, đúng 2 mã ≥+10% — người cầm tiền mặt cả kỳ đứng TRÊN trung vị thị trường.

**4. Ca đáng chú ý duy nhất — DCL 30/07 (`khác`, chi phí MH gate):** PRE_BREAKOUT BUY 63.8,
thắng +10.3% T+3, bị chặn bởi **MH halt (health 25)** khi regime đã caution — ca chi phí đầu
tiên của đèn vàng phase 2. Đối trọng cùng phiên: gate cũng chặn MCH FRESH (−3.4%) và MST
FRESH (−1.9%) → tổng của phiên bị chặn ≈ +1.7%, không đủ kết luận gate sai. Đánh dấu 👁 theo
dõi nhóm "bị MH gate chặn nhưng thắng"; health trễ sau đáy là điểm yếu đã biết (dist 25 phiên
tan chậm — MARKET_HEALTH.md §7). App đã nối lại khuyến nghị **ngay phiên regime ok đầu tiên
(03/08**, health 46 selective) và bắt lại DCL/MSB/SAB/VPI.

**Kết luận:** không sửa công thức. Đứng ngoài 20-31/07 là quyết định đúng về kỳ vọng (mọi
nhánh đối chứng đều âm trừ đúng đáy V — thứ hệ không thiết kế để bắt và dữ liệu không ủng hộ
bắt). Registry: 30 ca `loại_hợp_lệ` + DCL `khác` + 2 ca sót đợt cũ (HAH 9/7, VCG 13/7) — đã
đóng toàn bộ, unreviewed = 0. Dữ liệu replay: scratchpad `replay_norec.parquet` (tái tạo được
bằng script trong hồ sơ phiên làm việc 05/08).

---

## Đợt review 13/08/2026 — 125 ca (quan sát 30/07-07/08, sóng hồi rộng)

**119 ca `loại_hợp_lệ` — beta sóng hồi, không phải lỗi chọn mã:** toàn bộ là state NONE
(BUY 0) — GAS, GVR, BCM, PLX, PVT, GEX, FRT, DGC, BSR… còn sâu dưới pivot, đang leo lại từ
đáy 27/07. Trong cửa sổ này 58-70% pool cùng đạt ≥3% T+3 — khi 2/3 thị trường "thắng" thì
ngưỡng bỏ sót 3% mất ý nghĩa chọn lọc; đây là câu chuyện của cả pool, nhất quán với 2 đợt
review trước. Không hành động. (Cân nhắc tương lai: ngưỡng bỏ sót ĐỘNG — vd ret_t3 ≥ 3% VÀ
vượt median pool cùng phiên +X% — để tab bỏ sót không ngập trong beta ở các pha hồi rộng.)

**4 ca đặc biệt — và 3 trong đó chụm về đúng MỘT chỗ:**

| Ca | Engine thấy gì | Bị chặn bởi | Kết quả |
|---|---|---|---|
| DCL 31/07 | PRE **BUY 71.7** | MH **halt** (health 25) | **+14.4%** |
| NVL 03/08 | FRESH BUY 51.7 | MH **selective** (health 46, cần ≥65) | +4.2% |
| CTR 06/08 (đợt trước) | FRESH BUY 58.0 | MH **selective** (health 40) | trễ 1 phiên, +1.6% |
| HCM 07/08 | PRE 63.2 (EOD) | *không phải bỏ sót* — đã alert 10:14 intraday; obs EOD ghi selective | artifact sổ sách |

**Nhóm "MH chặn/trễ sau đáy" giờ = 4 ca** (DCL 30/07 +10.3 · DCL 31/07 +14.4 · NVL 03/08
+4.2 · CTR 06/08 trễ) so với 2 ca gate chặn đúng (MCH 30/07 −3.4 · MST 30/07 −1.9) — vượt
ngưỡng ≥3 ca của luật parsimony → **đủ điều kiện mở nghiên cứu calibrate MH hậu-đáy** (chờ
lệnh user). Ứng viên đã có sẵn mốc khách quan: **cửa sổ FTD** (FTD 30/07 bắn đúng lúc) —
biến thể "trong cửa sổ FTD: halt→selective" sẽ vớt DCL 31/07 (71.7≥65) và chặn vẫn đúng
MCH/DCL-30 (<65); kiểm chứng được trên `analysis/mh_phase2.py` + `ftd_study.py` trước khi
đụng config. Lưu ý HCM 07/08 còn lộ một nuance nhỏ: `is_reco` trong obs ghi theo health
EOD trong khi alert chạy theo health intraday — chấp nhận được (ghi nhận bảo thủ), nhưng
khi đọc thống kê obs cần nhớ nhóm is_reco=1 hơi thiếu so với alert thực.
