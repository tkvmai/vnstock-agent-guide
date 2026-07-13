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

*(Chưa có đợt review nào — dữ liệu toàn pool bắt đầu tích lũy từ phiên EOD 09/07/2026, ca
bỏ sót đầu tiên có thể review sau ~3 phiên, khoảng 14/07/2026.)*
