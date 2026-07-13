# Hướng Dẫn Nâng Cấp Hệ Thống (Migration Guide)

Tài liệu này cung cấp hướng dẫn nâng cấp toàn diện dành cho người dùng đang sử dụng thư viện `vnstock_pipeline` phiên bản cũ (v2.2.1 trở về trước), nhằm chuyển đổi sang:

1. **Kiến trúc Lưu trữ Tập trung** mới (v3.0+).
2. **Cấu trúc dữ liệu Streaming lấy cảm hứng từ chuẩn quốc tế (FIX/Bloomberg)**.

---

## PHẦN 1: Nâng Cấp Cấu Trúc Lưu Trữ

Trong các phiên bản trước, người dùng thường phải truyền thủ công đường dẫn `base_path` vào các `Exporter` (như `./data/ohlcv`), khiến dữ liệu bị phân mảnh và khó quản lý. Phiên bản mới áp dụng **Config Hub (`pipeline.toml`)** để quản lý vị trí lưu tự động.

### 1. Di Chuyển Dữ Liệu Cũ (Migration)

Nếu bạn có một thư mục dữ liệu cũ tự tạo và muốn đưa nó vào hệ thống quản lý tập trung mới, hãy sử dụng công cụ CLI tự động:

```bash
# Lệnh tự động nhận diện định dạng (Parquet/CSV), phân loại category và copy vào đúng thư mục cấu hình
python -m vnstock_pipeline.cli storage migrate-legacy --source ./old_data_folder
```

### 2. Chuyển Đổi Cấu Trúc Lưu Trữ (từ cấu trúc phẳng sang phân tầng)

Hệ thống hiện tại hỗ trợ 2 chế độ cấu trúc thư mục (Layout):

- **Flat (phẳng, mặc định)**: `~/vnstock_db/ohlcv/ACB.parquet`
- **Nested (phân cấp, tối ưu cho cơ sở dữ liệu chuyên nghiệp)**: `~/vnstock_db/raw/market/ohlcv/1D/equity/ACB.parquet`

Để chuyển đổi sang Layout `nested` hoặc ngược lại, bạn chỉ cần thay đổi cấu hình:

```bash
python -m vnstock_pipeline.cli storage set-layout nested
```

*Lưu ý: Mọi Pipeline chạy sau lệnh này sẽ tự động lưu dữ liệu theo cấu trúc mới. Nếu bạn muốn di chuyển các file hiện tại sang cấu trúc mới, hãy chạy lệnh `migrate-legacy` ở trên hướng về thư mục cũ.*

### 3. Đổi Định Dạng Hàng Loạt (CSV <-> Parquet)

Hệ thống cho phép bạn chuyển đổi qua lại giữa định dạng `csv` (dễ xem) và `parquet` (tối ưu dung lượng, tốc độ cao):

```bash
# Đổi hệ thống sang dùng Parquet làm mặc định
python -m vnstock_pipeline.cli storage set-format parquet
```

Khi chạy lệnh này, hệ thống sẽ tự động quét các file CSV cũ trong Database và hỏi bạn có muốn chuyển đổi toàn bộ sang Parquet hay không. Quá trình này sẽ sinh ra báo cáo dung lượng tiết kiệm được.

### 4. Loại bỏ các Module dư thừa (DuckDB / Firebase)

Bạn không cần cài đặt `duckdb` hoặc `firebase_admin` để chạy stream processors vì các class `DuckDBProcessor` và `FirebaseProcessor` dư thừa đã được gỡ bỏ khỏi thư viện. 
- Triết lý mới: **Pipeline chuyên trách gom dữ liệu vào Parquet/CSV hiệu năng cao**. Người dùng tự quản lý Data Warehouse (DuckDB) hoặc cơ sở dữ liệu thời gian thực (Firebase/Supabase) riêng lẻ tuỳ mục đích.
- Để truy vấn nhanh dữ liệu bên trong file parquet, hãy dùng lệnh CLI thay thế: `python -m vnstock_pipeline.cli inspect ~/vnstock_db/ohlcv/ACB.parquet`

---

## PHẦN 2: Nâng Cấp WebSocket Streaming Data

Cấu trúc dữ liệu trả về từ WebSocket trước đây (Legacy) có một số hạn chế về tính đồng nhất trong chuẩn đặt tên. Bản nâng cấp này giải quyết:

- Đồng nhất tất cả các key về dạng `snake_case` chuẩn.
- Loại bỏ các field viết tắt khó hiểu, chuẩn hóa hậu tố giá (`_price`), khối lượng (`_volume`) và giá trị (`_value`).
- Sổ lệnh (Board) được gộp tự động và phân loại mức giá theo bên Mua (`bid`) và Bán (`ask`).
- Đảm bảo tương thích hoàn toàn 100% với lớp giao diện `vnstock_data` và các agent phân tích tự động.

### Lộ Trình Nâng Cấp

Chúng tôi thiết kế lộ trình nâng cấp để không làm gián đoạn trải nghiệm của người dùng. Tùy thuộc vào nhu cầu, bạn có thể chọn 1 trong các tùy chọn sau:

#### Tùy Chọn 0: Giữ Nguyên Hiện Trạng (Không Cần Sửa Code)

Bạn không cần thay đổi bất kỳ dòng code nào. Hệ thống mặc định tiếp tục lưu trữ theo cấu trúc cũ.

```python
from vnstock_pipeline.stream import CSVProcessor
# Code cũ vẫn hoạt động hoàn hảo, mặc định là naming="legacy"
client.add_processor(CSVProcessor("data/{event_type}_%Y-%m-%d.csv"))
```

#### Tùy Chọn 1: Sử dụng Wrapper (Khuyến Nghị cho các Processors tự viết)

Nếu bạn đang dùng một Processor tự viết hoặc không muốn thay đổi tham số khởi tạo của Processor hiện tại, hãy dùng `StandardizedProcessor` để bọc nó lại.

```python
from vnstock_pipeline.stream import StandardizedProcessor, CSVProcessor

# 1. Khởi tạo processor như cũ
csv = CSVProcessor("data/{event_type}_%Y-%m-%d.csv")

# 2. Bọc processor bằng StandardizedProcessor
client.add_processor(StandardizedProcessor(csv))
```

#### Tùy Chọn 2: Sử Dụng Naming Parameter (Tối Ưu Nhất)

Đây là cách ngắn gọn và chính thức để sử dụng chuẩn mới. Chỉ cần truyền thêm `naming="standard"` khi khởi tạo `CSVProcessor`.

```python
from vnstock_pipeline.stream import CSVProcessor

# Truyền thêm tham số naming
client.add_processor(CSVProcessor(
    "data/{event_type}_%Y-%m-%d.csv",
    naming="standard"  # Kích hoạt output lấy cảm hứng từ chuẩn FIX/Bloomberg
))
```

### Kiểm Tra Sau Khi Nâng Cấp

Sau khi chuyển đổi sang `naming="standard"`, bạn cần cập nhật các script đọc/xử lý dữ liệu CSV của mình để khớp với tên cột mới.

**Ví dụ một số thay đổi thường gặp:**

- Thay vì đọc cột `timestamp`, hãy đọc cột `time`.
- Thay vì đọc `last_price` và `last_volume`, hãy dùng `price` và `volume`.
- Thay vì đọc `stock_id`, hãy dùng `id`.
- Với sổ lệnh cổ phiếu, sử dụng thẳng `bid_price_1`, `ask_price_1` thay vì phải tự lọc giá trị theo cột `side` và `price_1`.

Để tham khảo chi tiết toàn bộ các field bị thay đổi trong từng Event Type, vui lòng xem tài liệu:
👉 **[Standard Schema Reference](./07-streaming-data-schemas.md)**