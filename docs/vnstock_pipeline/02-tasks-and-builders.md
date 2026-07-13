# Vnstock Pipeline - Chi Tiết Tasks & Builders

## Giới Thiệu

Chương này cung cấp chi tiết về các **tasks** (quy trình sẵn có) và **builders** (cách xây dựng luồng tuỳ chỉnh) trong vnstock_pipeline. Các Tasks là những quy trình hoàn chỉnh (Lấy → Xác thực → Chuyển đổi → Xuất) được cung cấp sẵn để bạn dùng ngay. Builders là công cụ để tạo ra các pipeline tuỳ chỉnh theo ý muốn.

Tất cả các Task đều được kết nối chặt chẽ với **Kiến trúc Lưu trữ Tập trung**. Hệ thống tự động định tuyến file lưu trữ dựa trên biến cấu hình trong `pipeline.toml` thay vì bắt buộc truyền đường dẫn tuyệt đối thủ công.

---

## I. CÁC TÁC VỤ SẴN CÓ

### Task 1: OHLCV - Dữ Liệu Giá Hàng Ngày

**Mô tả**: Lấy dữ liệu OHLCV (mở, cao, thấp, đóng, khối lượng) lịch sử. Hỗ trợ cơ chế Smart Append, tự động cắt bỏ phần dữ liệu trùng và chỉ tải nối thêm ngày mới, hoặc phát hiện Split/Dividend để tự động tải lại toàn bộ.

**Các lớp (Classes)**:

- `OHLCVDailyFetcher`: Tự động tải dữ liệu từ `vnstock_data`. Tích hợp **cơ chế Fallback nguồn tự động**.
- `OHLCVValidator`: Chạy Non-blocking, bắt lỗi (VD: Giá cao nhất < Giá thấp nhất) và kích hoạt Schema Guard.
- `OHLCVDailyTransformer`: Xóa bản ghi trùng lặp theo thời gian.
- `ParquetExport` / `CSVExport`: Tự động lưu theo `StorageConfig`.

**Cơ chế Fallback Nguồn Tự Động:**
Hệ thống cho phép truyền vào một danh sách các nguồn (ví dụ: `["VCI", "KBS", "VND"]`). Khi thực thi, `Fetcher` sẽ ưu tiên lấy từ nguồn đầu tiên trong danh sách. Nếu nguồn đầu tiên bị lỗi kết nối hoặc trả về tập dữ liệu rỗng (Empty Data), hệ thống sẽ chủ động "fallback" (chuyển dự phòng) sang lấy dữ liệu từ nguồn tiếp theo trong danh sách cho đến khi thành công. Cơ chế này đặc biệt quan trọng để đảm bảo chuỗi dữ liệu giá lịch sử không bị đứt gãy.

**Tham Số**:

```python
run_task(
    tickers: list,                 # ['VCB', 'ACB', 'HPG']
    start: str = "2024-01-01",     # Ngày bắt đầu (Nếu bỏ trống, tự động kéo từ ngày kết thúc của file cũ)
    end: str = "2026-06-17",       # Ngày kết thúc
    interval: str = "1D",          # Khung thời gian: 1D, 1W, 1M...
    sources: list = ["VCI", "KBS", "VND"] # Danh sách nguồn ưu tiên để fallback
)
```

**Ví dụ Sử Dụng**:

```python
from vnstock_pipeline.tasks.ohlcv import run_task

run_task(['VCB', 'ACB', 'HPG'])
print("✅ Dữ liệu tự động lưu vào ~/vnstock_db/ohlcv/ (Định dạng Parquet mặc định)")
```

---

### Task 2: Dữ Liệu Tham Chiếu

**Mô tả**: Lấy thông tin cơ bản của toàn bộ mã cổ phiếu, chỉ số, chứng quyền trên thị trường chỉ với một lần chạy duy nhất (tải hàng loạt) thay vì duyệt qua vòng lặp. Dữ liệu này dùng để làm Universe cho các Pipeline khác.

**Tham Số**:

```python
run_reference_task()
```

**Ví dụ Sử Dụng**:

```python
from vnstock_pipeline.tasks.reference import run_reference_task

run_reference_task()
# Kết quả mặc định: ~/vnstock_db/reference/equity_by_exchange.parquet (và các nhóm khác)
```

---

### Task 3: Báo Cáo Tài Chính

**Mô tả**: Thu thập 5 bảng báo cáo tài chính (Cân đối kế toán, KQKD năm, KQKD quý, Lưu chuyển tiền tệ, Tỷ số).

**Hỗ trợ Multi-sheet Excel**: Nếu trong `pipeline.toml`, cấu hình `format_overrides` cho phần tài chính là `excel`, hệ thống sẽ gom cả 5 bảng vào chung 1 file Excel (VD: `ACB.xlsx` với 5 sheets). Nếu là `csv`, dữ liệu được chia nhỏ thành các file riêng biệt (VD: `ACB_balance_sheet.csv`). Hỗ trợ Smart Append giữ lại các hàng của các quý cũ khi cập nhật quý mới.

**Tham Số**:

```python
run_financial_task(
    tickers: list,
    balance_sheet_period: str = "year",
    income_statement_year_period: str = "year",
    income_statement_quarter_period: str = "quarter",
    cash_flow_period: str = "year",
    ratio_period: str = "year",
    lang: str = "vi",
    dropna: bool = True
)
```

---

### Task 4: Thống Kê Giao Dịch Nước Ngoài, Tự Doanh, Thoả Thuận và Nội Bộ

**Mô tả**: Đồng bộ lịch sử giá trị thống kê, giao dịch Khối ngoại, Tự doanh, và Thoả thuận.

**Tham Số**:

```python
run_trading_stats_task(
    tickers: list,
    data_types: list = ["trade_history", "foreign_flow", "proprietary_flow", "block_trades"]
)
```

*Lưu ý: Bạn có thể truyền chuỗi `["all"]` vào `data_types` để lấy toàn bộ 4 loại.*

---

### Task 5: Tin Tức & Sự Kiện

**Mô tả**: Thu thập dữ liệu báo chí từ các trang như CafeF, VNExpress, VNEconomy (được cấu hình nguồn qua `pipeline.toml`) và Lịch sự kiện doanh nghiệp (Trả cổ tức, ĐHĐCĐ).

**Lưu trữ**: 

- Tin tức được lưu theo dạng báo chí tổng hợp: `news/cafef.parquet`, `news/vnexpress.parquet`. Chống trùng bài viết dựa trên URL.
- Sự kiện được lưu theo năm (Phân mảnh theo khối lượng lớn): `events/dividend_2024.parquet`.

**Ví dụ**:

```python
from vnstock_pipeline.tasks.news import run_news_task
from vnstock_pipeline.tasks.events import run_events_task

# Tải tin tức (Các nguồn mặc định tự lấy từ file pipeline.toml)
run_news_task()

# Tải sự kiện doanh nghiệp (Cập nhật lịch trả cổ tức hằng ngày)
run_events_task(mode="daily")
```

---

### Task 6: Giao Dịch Trong Phiên & Bảng Giá

- **Intraday**: Thu thập lịch sử lệnh khớp tick-by-tick. Trình `IntradayValidator` hỗ trợ phát hiện mất dữ liệu theo phút, cảnh báo khoảng trống thời gian.
- **Price Board**: Truy xuất giá hiện hành, khối lượng mua/bán ở các bước giá tốt nhất (Hỗ trợ chế độ Live cập nhật 10 giây/lần hoặc chế độ End-of-Day).

---

## II. XÂY DỰNG QUY TRÌNH TÙY CHỈNH

Bên cạnh các Task dựng sẵn, bạn có thể tự viết các Component và lắp ráp chúng qua `Scheduler`.

### 1. Trình Thu Thập Tùy Chỉnh Cơ Bản

**Mục đích**: Fetch dữ liệu từ API riêng hoặc tự tuỳ biến các trường trả về.

```python
from vnstock_pipeline.template.vnstock import VNFetcher
import pandas as pd

class MyFetcher(VNFetcher):
    def _vn_call(self, ticker: str, **kwargs) -> pd.DataFrame:
        from vnstock_data.ui.market import Market
        market = Market(source="vci").equity(ticker)
        
        # Chỉ lấy 1 khoảng thời gian fix cố định
        df = market.history(start="2024-01-01", end="2026-06-17", interval="1D")
        
        # Thêm cột tùy chỉnh
        df['my_custom_source'] = "custom_pipeline"
        return df
```

### 2. Trình Kiểm Duyệt & Biến Đổi Tùy Chỉnh

**Mục đích**: Đưa logic kiểm duyệt riêng vào luồng chạy và làm sạch/làm giàu dữ liệu như thêm các chỉ báo kỹ thuật SMA, RSI.

```python
from vnstock_pipeline.template.vnstock import VNValidator, VNTransformer

class TechIndicatorTransformer(VNTransformer):
    def transform(self, data: pd.DataFrame) -> pd.DataFrame:
        df = super().transform(data)
        if len(df) > 20:
            df['sma_20'] = df['close'].rolling(20).mean()
        return df
```

### 3. Exporter với Kiến trúc lưu trữ tập trung

Từ phiên bản mới nhất, thay vì tự gọi `df.to_csv()`, bạn nên dùng các class `ParquetExport` hoặc `CSVExport` có sẵn trong thư viện. Bạn chỉ cần khai báo `category` (hoặc `data_type`), Exporter sẽ **tự động nối với Storage Hub (`pipeline.toml`)** để lấy ra cấu trúc đường dẫn đúng chuẩn (Flat hay Nested).

```python
from vnstock_pipeline.core.exporter import CSVExport
from vnstock_pipeline.core.scheduler import Scheduler

# Chỉ cần khai báo category, đường dẫn tuyệt đối (base_path) được tự động nạp!
exporter = CSVExport(category="my_custom_tech_data")

scheduler = Scheduler(
    MyFetcher(),
    VNValidator(),  # Dùng base validator (chỉ pass dữ liệu)
    TechIndicatorTransformer(),
    exporter,
    max_workers=3,
    request_delay=0.5
)

scheduler.run(['ACB', 'VCB', 'HPG'])
```

Khi chạy kịch bản trên, kết quả sẽ được lưu tự động tại `~/vnstock_db/my_custom_tech_data/ACB.csv` (nếu dùng Flat layout) mà không cần bận tâm khai báo thư mục.