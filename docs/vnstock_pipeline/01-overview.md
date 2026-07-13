# Vnstock Pipeline - Tổng Quan Kiến Trúc

## Giới Thiệu

`vnstock_pipeline` là thư viện Python cung cấp một framework mạnh mẽ và linh hoạt để xây dựng các **luồng xử lý dữ liệu** cho thị trường chứng khoán Việt Nam. Thư viện bao quát toàn bộ quy trình từ bước thu thập, xác thực, chuyển đổi cho đến xuất dữ liệu thành phẩm. Với kiến trúc **mô-đun** và **lưu trữ tập trung**, người dùng có thể dễ dàng quản lý khối lượng dữ liệu khổng lồ mà không cần cấu hình phức tạp.

### Đặc Điểm Nổi Bật

- **Kiến trúc Lưu trữ Tập trung**: Quản lý vị trí lưu trữ, định dạng (Parquet/CSV/Excel) và cấu trúc (phẳng/phân cấp) từ một file duy nhất `pipeline.toml`.
- **Bảo Vệ Cấu Trúc**: Các lớp Validator chạy ngầm giúp bắt lỗi dữ liệu rỗng, gián đoạn thời gian, và tự động cách ly các file làm sai lệch cấu trúc CSDL vào `.tmp/.quarantine/`.
- **Bộ Công Cụ Quản Trị CLI**: Hỗ trợ kiểm tra, di chuyển dữ liệu, chuyển đổi định dạng và dọn dẹp dữ liệu cũ hoàn toàn bằng dòng lệnh.
- **Tác vụ dựng sẵn**: Bao phủ đầy đủ từ Giá (OHLCV, Intraday, Bảng giá) đến Tài chính, Thống kê giao dịch, Dữ liệu Cơ sở, Tin tức và Sự kiện doanh nghiệp.
- **Tự động lập lịch & Xử lý lỗi**: Điều phối tự động tải hàng loạt mã chứng khoán, ghi log nhất quán (`pipeline_latest.log`) và báo lỗi ra `error_log.csv` để tự động chạy lại.

---

## Cấu Trúc Luồng Xử Lý Dữ Liệu

```
Dữ liệu thô (API/WebSocket)
    │
    ▼
[Fetcher] ─► Thu thập dữ liệu (theo nhóm hoặc từng mã)
    │
    ▼
[Validator] ─► Kiểm tra tính toàn vẹn, bắt lỗi cấu trúc, ghi nhận cấu trúc dữ liệu
    │
    ▼
[Transformer] ─► Chuyển đổi, làm sạch, chống trùng lặp
    │
    ▼
[Exporter] ─► Kết nối tự động với `StorageConfig` -> Ghi ra Parquet/CSV/Excel
    │
    ▼
[Storage] ─► Thư mục lưu trữ (~/vnstock_db) & [Metadata Manager] cập nhật Catalog
```

**Scheduler** điều phối quá trình trên:

- Tự động chia luồng xử lý song song giúp tăng tốc độ thực thi cho nhiệm vụ cập nhật dữ liệu gấp nhiều lần.
- Tự động nhận diện dữ liệu bị rỗng để bỏ qua, không làm tốn thời gian thử lại.
- Ghi log (INFO/DEBUG) và xuất `error_log.csv` đối với các mã bị lỗi gián đoạn.

---

## Kiến Trúc Lưu Trữ Tập Trung

Hệ thống quản lý thông số thông qua file cấu hình trung tâm tại:

- **Mac/Linux:** `~/.vnstock/config/pipeline.toml`
- **Windows:** `%USERPROFILE%\.vnstock\config\pipeline.toml`

Thay vì truyền `base_path` thủ công vào lớp `Exporter` như các phiên bản cũ, giờ đây mọi Exporter trong thư viện đều đọc tự động cấu hình từ `pipeline.toml`.

### Tính năng cốt lõi:

1. **Format linh hoạt**: Mặc định lưu `parquet` để tối ưu dung lượng, nhưng có thể thiết lập `format_overrides` để xuất Báo cáo tài chính ra `excel` hoặc dữ liệu sổ lệnh, thống kê ra `csv` tiện sử dụng.
2. **Cấu Trúc Linh Hoạt (Flat vs Nested Layout)**: Hệ thống cung cấp hai chế độ tổ chức thư mục linh hoạt để phù hợp với từng Use Case:

   **Chế độ Flat (Mặc định)**: Dành cho người dùng cá nhân, lược bỏ các cấp thư mục thừa để truy xuất file dễ nhất.
   ```text
   ├── stock_db/
   │   ├── ohlcv/                          # Dữ liệu nến ngày
   │   │   ├── ACB.parquet
   │   ├── trades/                         # Dữ liệu khớp lệnh (Tick)
   │   │   ├── ACB.parquet
   │   ├── session_stats/                  # Thống kê giao dịch
   │   │   ├── foreign_flow/
   │   │   │   └── ACB.parquet
   │   ├── financial/                      # Dữ liệu BCTC
   │   │   ├── ACB_balance_sheet.parquet
   │   ├── news/                           # Dữ liệu tin tức
   │   │   ├── cafef.parquet
   │   ├── events/                         # Dữ liệu sự kiện
   │   │   ├── calendar_events.parquet
   │   ├── reference/                      # Dữ liệu cấu trúc/Tham chiếu
   │   │   ├── list/                       
   │   │   │   └── equity_by_exchange.parquet
   │   │   ├── company/                    
   │   │   │   ├── info/
   │   │   │   │   └── ACB.parquet
   ```

   **Chế độ Nested**: Lý tưởng cho Enterprise Data Lake, tổ chức chặt chẽ theo lớp metadata (`[Base]/[Layer]/[Domain]/[Category]/[Interval]/[Instrument]/[Date]`). Tích hợp sẵn **Smart Defaults** để tối ưu cấu trúc (vd: `session_stats` tự bỏ cấp interval, `trades` ép kiểu `tick`).
   ```text
   ├── stock_db/
   │   ├── processed/                      # DataLayer
   │   │   ├── market/                     # DataDomain
   │   │   │   ├── ohlcv/                  # Category
   │   │   │   │   ├── 1D/                 # Interval: Khung thời gian (Ngày)
   │   │   │   │   │   ├── equity/         # Instrument: Loại tài sản
   │   │   │   │   │   │   └── ACB.parquet
   │   │   │   ├── session_stats/          # Category (Bỏ qua Interval vì mặc định là EOD)
   │   │   │   │   ├── foreign_flow/
   │   │   │   │   │   ├── equity/
   │   │   │   │   │   │   └── ACB.parquet
   │   │   │   ├── trades/                 # Category
   │   │   │   │   ├── tick/               # Interval: Dữ liệu Tick
   │   │   │   │   │   ├── equity/
   │   │   │   │   │   │   ├── 2026-06-20/ # Date: Phân mảnh theo ngày (Partitioning)
   │   │   │   │   │   │   │   └── ACB.parquet
   │   │   ├── fundamental/                # DataDomain
   │   │   │   ├── balance_sheet/          
   │   │   │   │   ├── 1Y/                 # Interval: Khung năm
   │   │   │   │   │   ├── equity/         
   │   │   │   │   │   │   └── ACB.parquet
   ```

3. **Metadata Catalog**: Khi dữ liệu được tải xong, `MetadataManager` âm thầm cập nhật thông tin độ lớn, độ dải ngày vào `_metadata/catalog/`.

---

## Các Loại Tác Vụ Dựng Sẵn

Hệ thống cung cấp sẵn các mẫu pipeline đáp ứng đầy đủ nhu cầu của một Data Warehouse chứng khoán:

### 1. Dữ Liệu Tham Chiếu

Truy xuất toàn bộ danh mục tài sản thị trường (Cổ phiếu, Chỉ số, Phái sinh).

- **Kết quả**: `reference/equity_by_exchange.parquet`

### 2. Dữ Liệu OHLCV cho phân tích kỹ thuật

Dữ liệu giá lịch sử kết thúc phiên. Có cơ chế Append/Merge và kiểm duyệt (High > Low).

- **Kết quả**: `ohlcv/{ticker}.parquet`

### 3. Báo Cáo Tài Chính

5 bảng (Cân đối kế toán, KQKD, Lưu chuyển tiền tệ, Tỷ số).

- **Kết quả**: Xuất chung 5 bảng vào `financial/{ticker}.xlsx` (nếu dùng excel) hoặc chia ra nhiều file `.csv`. Hỗ trợ Smart Append dữ liệu quý mới.

### 4. Dữ Liệu Khớp Lệnh & Bảng Giá

- **Intraday**: Lấy dữ liệu tick. Hỗ trợ Validator phát hiện mất sóng (gap), độ trễ.
- **Price Board**: Cập nhật giá liên tục.

### 5. Thống Kê Giao Dịch & Giao Dịch Tổ Chức

Lịch sử mua/bán khối ngoại, tự doanh, thoả thuận.

- **Kết quả**: `trading_stats/foreign_flow/{ticker}.parquet`

### 6. Tin Tức & Sự Kiện

- **News**: Lấy tin từ nhiều nguồn, hợp nhất, xoá trùng lặp, dùng buffer tạm thời (`.tmp`) trước khi đổ vào Parquet.
- **Events**: Lịch trả cổ tức, ĐHĐCĐ. Lưu thành dạng Flat file phân mảnh theo năm (VD: `events/dividend_2024.parquet`).

---

## Bảo Vệ Cấu Trúc

Hệ thống tự động phát hiện và bảo vệ Database khỏi các lỗi thay đổi cấu trúc dữ liệu âm thầm (Schema Drift):

- **Schema Registry**: Tự động ghi nhận `Baseline Schema` cho từng `category` ở lần tải thành công đầu tiên, lưu tại `_metadata/schemas/`.
- **Schema Guard**: Trái tim so sánh cấu trúc mới và cũ.
  - Phân loại `Safe`: Dữ liệu khớp 100% -> Chấp nhận ghi.
  - Phân loại `Warning` (Additive): Dữ liệu có thêm cột mới -> Vẫn ghi bình thường, chỉ log cảnh báo.
  - Phân loại `Breaking`: Dữ liệu thiếu cột gốc, hoặc sai kiểu (dtype) -> Từ chối ghi đè, đẩy vào vùng cách ly.
- **Quarantine Manager**: Dữ liệu lỗi bị giam lỏng tại `[base_path]/.tmp/.quarantine/` kèm theo file `.diff.json` giải thích nguyên nhân. Các file này sẽ tự động dọn dẹp sau 14 ngày.
- **Validators Logic**: Chạy Non-blocking. Ví dụ `OHLCVValidator` chặn lưu giá trần thấp hơn giá sàn; `IntradayValidator` bắt lỗi mất dữ liệu giữa phiên. Rổ VN30 sẽ được giám sát khắt khe hơn.

---

## Cấu Trúc Enum Và Hằng Số (`storage_enums.py`)

Bộ framework chuẩn hoá toàn bộ các định nghĩa danh pháp để tránh sai sót:
- `DataLayer`: `raw`, `processed`, `insights`.
- `DataDomain`: `market`, `fundamental`, `reference`, `alternative`, v.v.
- `InstrumentType`: `equity`, `index`, `derivatives`, `etf`, `warrant`, `bond`, `fund`, `crypto`, `forex`, `commodity`.
- `StorageFormat`: `parquet`, `csv`.
- `LayoutMode`: `flat`, `nested`.

---

## Cách Bắt Đầu?

Hệ thống đã thiết kế để bạn bắt đầu cực kỳ nhanh chóng. Bạn có thể:

1. Đọc tiếp [Cách Sử Dụng Tasks và Builders](02-tasks-and-builders.md) để biết cách chạy Pipeline.
2. Hoặc đọc ngay [Vận Hành bằng CLI & Các Use Case Thực Tế](05-cli-and-use-cases.md) để thử nghiệm tải dữ liệu không cần viết code!