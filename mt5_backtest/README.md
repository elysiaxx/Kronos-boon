# Kronos × MetaTrader 5 — Backtest đo độ chính xác

Bộ công cụ này đo **độ chính xác dự báo** của mô hình Kronos trên dữ liệu
nến thật lấy từ tài khoản **MetaTrader 5 (demo hoặc real)**, theo phương pháp
**walk-forward** (trượt cửa sổ, không nhìn trước tương lai).

Mặc định cấu hình cho **XAUUSD khung M15**, nhưng đổi được sang bất kỳ
symbol / khung thời gian nào MT5 của bạn có.

> ⚠️ **Lưu ý quan trọng về môi trường**
> Thư viện Python `MetaTrader5` chỉ chạy trên **Windows** và cần một
> **terminal MT5 đang mở + đã đăng nhập** trên cùng máy. Vì vậy phần lấy dữ
> liệu trực tiếp từ MT5 phải chạy trên máy Windows (hoặc VPS Windows) của bạn.
> Nếu không có MT5 trên máy đang chạy, dùng chế độ `--source csv` với file đã
> export (xem bên dưới) — chế độ CSV chạy trên mọi hệ điều hành.

---

## 1. Cài đặt

Trên máy **Windows có MT5**:

```bash
# từ thư mục gốc repo Kronos
pip install -r requirements.txt                 # numpy, pandas, torch, matplotlib, huggingface_hub...
pip install -r mt5_backtest/requirements-mt5.txt # MetaTrader5 (chỉ Windows)
```

Trong terminal MT5: **Tools → Options → Expert Advisors**, bật *Allow Algo
Trading*; và kéo symbol **XAUUSD** vào *Market Watch* để MT5 tải lịch sử nến.

Lấy thông tin tài khoản demo từ broker (login dạng số, password, server).

---

## 2. Chạy backtest trực tiếp từ MT5

```bash
python -m mt5_backtest.backtest \
    --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24 --n_windows 50 --step 24 \
    --model NeoQuasar/Kronos-small \
    --mt5_login 1234567 --mt5_password "DEMO_PASS" --mt5_server "YourBroker-Demo"
```

Nếu terminal MT5 đã đăng nhập sẵn, có thể bỏ 3 tham số `--mt5_*` — script sẽ
dùng luôn phiên đang mở.

Ý nghĩa tham số chính:

| Tham số        | Mặc định | Ý nghĩa |
|----------------|----------|---------|
| `--lookback`   | 400      | Số nến lịch sử đưa vào mô hình (≤ context: 512 với small/base) |
| `--pred_len`   | 24       | Số nến dự báo mỗi cửa sổ (24 nến M15 = 6 giờ) |
| `--n_windows`  | 30       | Số cửa sổ walk-forward (giữ các cửa sổ gần nhất) |
| `--step`       | 24       | Bước trượt giữa 2 cửa sổ (`= pred_len` ⇒ không chồng lấn) |
| `--eval_horizon` | pred_len | Mốc nến dùng cho chỉ số hướng/chiến lược (1-based) |
| `--sample_count` | 1      | Số mẫu lấy trung bình mỗi cửa sổ (cao hơn ⇒ ổn định hơn) |
| `--T` / `--top_p` | 1.0 / 0.9 | Nhiệt độ & nucleus sampling (giảm T ⇒ xác định hơn) |
| `--seed`       | None     | Cố định ngẫu nhiên để lặp lại được |

---

## 3. Chạy bằng CSV (mọi hệ điều hành)

Trên máy Windows, export nến ra CSV một lần:

```bash
python -m mt5_backtest.export_mt5_csv --symbol XAUUSD --timeframe M15 --count 8000 --out xauusd_m15.csv
```

Rồi chạy backtest ở bất cứ đâu (không cần MT5):

```bash
python -m mt5_backtest.backtest --source csv --csv xauusd_m15.csv \
    --symbol XAUUSD --timeframe M15 --lookback 400 --pred_len 24 --n_windows 50
```

Chế độ CSV cũng nhận file xuất tay từ MT5 (các cột `time, open, high, low,
close, tick_volume, real_volume, spread` đều được tự nhận diện).

---

## 4. Kết quả

Script in ra bảng tóm tắt và lưu vào `mt5_backtest/results/`:

* `<symbol>_<tf>_<time>_report.json` — toàn bộ số liệu.
* `..._sample_window.png` — một cửa sổ mẫu: lịch sử + thực tế + dự báo.
* `..._dir_accuracy.png` — độ chính xác hướng theo từng mốc dự báo.
* `..._equity_curve.png` — đường vốn của chiến lược "theo hướng" đơn giản.

Các chỉ số:

* **MAE / RMSE / MAPE** — sai số của đường giá `close` dự báo so với thực tế.
* **Directional accuracy** — tỉ lệ mô hình đoán đúng **hướng** (lên/xuống) so
  với giá đóng cửa cuối cùng quan sát được. **50% = tung đồng xu**; cao hơn
  đáng kể và ổn định mới có giá trị.
* **Naive strategy** — long/short thuần theo hướng dự báo tại `eval_horizon`,
  quy ra % lợi nhuận để biết dự báo có *dùng được* không. Đây **không phải
  khuyến nghị đầu tư**, chỉ là một thước đo.

---

## 5. Lưu ý & giới hạn

* **Kronos sinh mẫu ngẫu nhiên.** Chạy 2 lần có thể lệch nhau. Dùng `--seed`
  để lặp lại, và tăng `--sample_count` (vd 5–10) để kết quả ổn định hơn.
* **Không có nhìn trước (look-ahead).** Mỗi dự báo chỉ thấy nến *trước* đoạn
  được chấm điểm; phần tương lai được giữ kín để so sánh.
* **Múi giờ.** Thời gian nến lấy theo giờ server của MT5. Kronos chỉ dùng đặc
  trưng chu kỳ (phút/giờ/thứ/ngày/tháng) nên không ảnh hưởng miễn là nhất quán.
* **Volume.** XAUUSD/forex thường không có volume thật (`real_volume = 0`) nên
  dùng `tick_volume`; `amount` để Kronos tự suy ra như mặc định thư viện.

---

## 6. Forward-test / giao dịch demo (`live_demo.py`)

Sau khi đo độ chính xác, bạn có thể chạy **vòng lặp realtime**: mỗi khi một
nến đóng → Kronos dự báo → ra tín hiệu long/short/flat → vào/ra lệnh → ghi log
equity.

Có 2 trục độc lập:

* `--broker paper` (mặc định) mô phỏng lệnh trong bộ nhớ, **không gửi gì**;
  `--broker mt5` gửi lệnh thật qua `order_send`.
* `--feed mt5` lấy nến realtime từ terminal; `--feed replay --csv ...` phát lại
  một file CSV như thể nến đang về (chạy offline, không cần MT5).

**Chốt an toàn:** mặc định là `paper`; `--broker mt5` bắt buộc đi với
`--feed mt5`; và `MT5Broker` **từ chối tài khoản real** trừ khi thêm
`--allow_real`.

Quy trình đề xuất — thử 3 bước từ an toàn đến thật:

```bash
# 1) Dry-run hoàn toàn offline trên CSV (không cần MT5):
python -m mt5_backtest.live_demo --broker paper --feed replay \
    --csv xauusd_m15.csv --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24 --eval_horizon 8 --threshold_pct 0.1 \
    --volume 0.01 --contract_size 100

# 2) Paper-trade trên dữ liệu LIVE (dữ liệu thật, lệnh mô phỏng):
python -m mt5_backtest.live_demo --broker paper --feed mt5 \
    --symbol XAUUSD --timeframe M15 --max_bars 96

# 3) Giao dịch trên tài khoản DEMO thật:
python -m mt5_backtest.live_demo --broker mt5 --feed mt5 \
    --symbol XAUUSD --timeframe M15 --volume 0.01 --max_bars 96 \
    --flatten_on_exit \
    --mt5_login 1234567 --mt5_password "DEMO_PASS" --mt5_server "YourBroker-Demo"
```

Tham số đáng chú ý: `--eval_horizon` (nến dùng để ra tín hiệu), `--threshold_pct`
(ngưỡng % tối thiểu mới vào lệnh — nhỏ hơn ⇒ giao dịch nhiều hơn), `--volume`
(khối lượng mỗi lệnh), `--max_bars` / `--duration_min` (điều kiện dừng),
`--flatten_on_exit` (đóng lệnh khi dừng). Dừng bất cứ lúc nào bằng `Ctrl+C`.

Mỗi quyết định được ghi vào `results/..._trades.csv` (giờ nến, hướng, hành
động, giá khớp, equity). Chế độ paper in thêm số lệnh đóng, tỉ lệ thắng và % lãi.

> Đây là một chiến lược *tối giản* để kiểm tra chất lượng dự báo trong điều
> kiện thật, **không phải khuyến nghị đầu tư**. Chỉ dùng trên tài khoản demo.

---

## 7. Xem dự báo NGAY TRÊN biểu đồ MT5 (`mt5_visualize.py` + `KronosForecast.mq5`)

MetaTrader 5 không cho Python vẽ lên chart — chỉ **indicator MQL5** chạy trong
terminal mới vẽ được. Nên ở đây dùng một cầu nối đơn giản:

```
Python (Kronos)  →  kronos_forecast.csv  →  indicator KronosForecast  →  vẽ lên chart
```

Python dự báo rồi ghi file CSV vào thư mục **Common\Files** của MT5; indicator
đọc file đó và vẽ **đường giá đóng cửa dự báo, nến dự báo, dải high/low** nối
dài sang phải (tương lai), cùng một vạch dọc đánh dấu điểm bắt đầu dự báo.

### Cài indicator (một lần)

1. Mở **MetaEditor** (nút trên MT5, hoặc F4).
2. Copy `mt5_backtest/mql5/KronosForecast.mq5` vào thư mục
   `MQL5/Indicators/` (trong MetaEditor: chuột phải *Indicators → Open Folder*).
3. Chọn file và bấm **Compile** (F7) — ra `KronosForecast.ex5`.

### Chạy

Trên máy Windows, bật Python ghi forecast (tự tìm thư mục Common\Files):

```bash
# một lần:
python -m mt5_backtest.mt5_visualize --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24

# cập nhật mỗi khi có nến M15 mới:
python -m mt5_backtest.mt5_visualize --symbol XAUUSD --timeframe M15 --loop
```

Trên MT5: mở chart **XAUUSD, khung M15**, kéo indicator **KronosForecast** từ
*Navigator* vào chart. Dự báo hiện ngay; khi chạy `--loop` nó tự làm mới theo
`InpRefreshSec` giây.

Tùy chọn của indicator: `InpDrawCandles` (nến dự báo), `InpDrawBand` (dải
high/low), `InpCloseColor`/`InpLineWidth` (đường close), `InpFile` (tên file —
phải trùng `--filename` bên Python, mặc định `kronos_forecast.csv`).

### Lưu ý

* **Khớp symbol & khung thời gian** giữa Python và chart, nếu không mốc thời
  gian dự báo sẽ lệch.
* Dự báo nằm ở vùng tương lai (bên phải nến cuối). Nếu không thấy, bật **Chart
  shift** (nút dịch biểu đồ trên thanh công cụ) để chừa khoảng trống bên phải.
* Thời gian dùng epoch theo **giờ server MT5** để khớp đúng với nến trên chart.
* Muốn kiểm tra nhanh không cần MT5: ghi file ra chỗ bất kỳ bằng
  `--feed replay --csv data.csv --out kronos_forecast.csv` rồi tự xem.

---

## 8. Dùng trên macOS (không có package `MetaTrader5`)

Thư viện Python `MetaTrader5` **chỉ chạy trên Windows**. Trên Mac, MT5 chạy qua
**Wine** (bản "MetaTrader 5 for Mac" của sàn). Vì vậy **`--source mt5` và
`--broker mt5` không dùng được native trên Mac**. Nhưng Kronos (Python + torch)
chạy native rất tốt trên Mac (kể cả MPS trên Apple Silicon), và phần **xem dự
báo trên chart vẫn chạy đầy đủ** qua **cầu nối file**, không cần package đó:

```
MT5 (Wine) ──KronosExport.mq5──▶ kronos_bars.csv
                                      │  (Python Kronos native macOS đọc)
                                      ▼
                               dự báo bằng Kronos
                                      │
                 kronos_forecast.csv ◀┘
                                      │
MT5 (Wine) ──KronosForecast.mq5 đọc & vẽ lên chart ◀┘
```

### Các bước trên Mac

1. **Cài 2 indicator** `KronosExport.mq5` và `KronosForecast.mq5` vào
   `MQL5/Indicators/` rồi Compile (xem mục 7). Trong MT5: *File → Open Data
   Folder* để tới thư mục dữ liệu (nằm trong Wine).
2. **Tìm thư mục `Common\Files`** để Python đọc/ghi:
   ```bash
   python -m mt5_backtest.mt5_visualize --find_mt5
   ```
   (hoặc trong MT5: *File → Open Data Folder* → lùi ra `Terminal\Common\Files`.)
3. **Kéo cả 2 indicator** vào chart XAUUSD M15. `KronosExport` sẽ ghi
   `kronos_bars.csv` vào `Common\Files`.
4. **Chạy Kronos native trên macOS**, theo dõi file nến và ghi dự báo:
   ```bash
   COMMON="/duong/dan/toi/Terminal/Common/Files"   # lấy từ bước 2
   python -m mt5_backtest.mt5_visualize --feed file \
       --bars_csv "$COMMON/kronos_bars.csv" \
       --symbol XAUUSD --timeframe M15 --lookback 400 --pred_len 24 --loop
   ```
   `KronosForecast` trên chart sẽ tự vẽ dự báo và cập nhật mỗi nến mới.

### Backtest độ chính xác trên Mac

Cũng dùng file nến đó (hoặc export history từ MT5 ra CSV):

```bash
python -m mt5_backtest.backtest --source csv \
    --csv "$COMMON/kronos_bars.csv" --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24 --n_windows 50
```

### Còn lại gì?

* **Vẽ dự báo + backtest trên Mac: chạy đầy đủ** bằng cầu nối file ở trên.
* **Đặt lệnh tự động (`order_send`) trên Mac:** cần package Windows-only, nên
  phải chạy vòng giao dịch trên **Windows VM/VPS**, hoặc viết một EA MQL5 đọc
  file tín hiệu rồi tự đặt lệnh (thuần trong terminal, không cần Python). Nói
  một tiếng nếu bạn muốn mình làm EA đó.
