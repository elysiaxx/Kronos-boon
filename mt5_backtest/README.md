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

* **`..._report.html` — mở file này trước.** Báo cáo tương tác, tự giải thích:
  xem được **từng** cửa sổ dự báo vẽ đè lên giá thật (kéo thanh trượt, hoặc bấm
  "lần tệ nhất / tốt nhất"), độ chính xác theo từng mốc, đường vốn, bảng chi
  tiết 1 dòng = 1 lần dự báo, và một mục giải nghĩa mọi chỉ số. File đứng độc
  lập (không cần mạng, không cần thư viện) — bấm đúp là xem được bằng trình
  duyệt.
* `..._report.json` — toàn bộ số liệu, để xử lý tiếp bằng script.
* `..._detail.json` — số liệu + dữ liệu từng cửa sổ. Dựng lại trang HTML từ file
  này mà **không cần chạy lại mô hình**:

  ```bash
  python -m mt5_backtest.report_html mt5_backtest/results/XAUUSD_M15_..._detail.json
  ```

* `..._sample_window.png` — một cửa sổ mẫu: lịch sử + thực tế + dự báo.
* `..._dir_accuracy.png` — độ chính xác hướng theo từng mốc dự báo.
* `..._equity_curve.png` — đường vốn của chiến lược "theo hướng" đơn giản.

Tắt bớt đầu ra nếu không cần: `--no_html` (bỏ trang HTML), `--no_plots` (bỏ
PNG). `--hist_tail N` chỉnh số nến quá khứ vẽ kèm mỗi cửa sổ trong trang HTML
(mặc định 80, chỉ ảnh hưởng hiển thị).

Các chỉ số:

* **MAE / RMSE / MAPE** — sai số của đường giá `close` dự báo so với thực tế.
* **Directional accuracy** — tỉ lệ mô hình đoán đúng **hướng** (lên/xuống) so
  với giá đóng cửa cuối cùng quan sát được. **50% = tung đồng xu**; cao hơn
  đáng kể và ổn định mới có giá trị.
* **Khoảng tin cậy 95% + p-value** — in kèm ngay dưới directional accuracy. Với
  vài chục cửa sổ, 58% và 50% gần như không phân biệt được: nếu `p > 0.05` thì
  kết quả **chưa** loại được khả năng chỉ do may mắn. Muốn kết luận chắc hơn thì
  tăng `--n_windows` (hoặc chạy trên nhiều đoạn dữ liệu khác nhau).
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

* **Lệnh** — `--broker` quyết định lệnh đi đâu:

  | `--broker` | Lệnh đi đâu | Chạy được trên |
  |---|---|---|
  | `paper` (mặc định) | mô phỏng trong bộ nhớ, **không gửi gì** | mọi hệ điều hành |
  | `file` | **lệnh thật**, do EA `KronosTrader.mq5` đặt trong terminal | macOS / Linux / Windows |
  | `mt5` | **lệnh thật**, qua `order_send` của package Python | chỉ Windows |
* **Dữ liệu** — `--feed` chọn nến ở đâu ra:

  | `--feed` | Nguồn nến | Chạy được trên | Cần gì |
  |---|---|---|---|
  | `mt5` | terminal MT5 qua package `MetaTrader5` | **chỉ Windows** | MT5 đang mở |
  | `file` | CSV do `KronosExport.mq5` ghi ra | macOS / Linux / Windows | MT5 (kể cả bản Wine cho Mac) + indicator |
  | `yahoo` | Yahoo Finance | mọi hệ điều hành | chỉ cần `pip install yfinance` |
  | `replay` | phát lại một file CSV | mọi hệ điều hành | một file CSV |

**Chốt an toàn:** mặc định là `paper`; `--broker mt5` bắt buộc đi với
`--feed mt5`; và `MT5Broker` **từ chối tài khoản real** trừ khi thêm
`--allow_real`.

### Trên macOS

**Terminal MT5 cài được trên Mac** (bản "MetaTrader 5 for Mac" của sàn, đóng gói
Wine). Thứ không có bản macOS là **thư viện Python `MetaTrader5`** — PyPI chỉ có
wheel Windows:

```
$ pip download MetaTrader5 --no-deps
ERROR: Could not find a version that satisfies the requirement MetaTrader5 (from versions: none)
```

Nên `--broker mt5` / `--feed mt5` (gọi thẳng API Python) không dùng được trên
Mac. Đường vòng là để **terminal tự làm phần của terminal**: Python ghi file,
MQL5 đọc file. Có 2 mức:

**(a) Lệnh thật trên tài khoản demo** — `--broker file`:

```
Kronos (Python native, có MPS)
  ├── đọc nến  ◀── kronos_bars.csv   ◀── KronosExport.mq5
  └── ghi tín hiệu ─▶ kronos_signal.csv ─▶ KronosTrader.mq5 ─▶ OrderSend()
                      kronos_status.csv ◀─ (tài khoản, vị thế, lãi lỗ từng deal)
```

```bash
# Một lần: cài KronosExport.mq5 + KronosTrader.mq5 (mục 7), bật AutoTrading.
COMMON=$(python -m mt5_backtest.mt5_visualize --find_mt5 | head -1)

python -m mt5_backtest.live_demo --broker file --feed file \
    --bars_csv "$COMMON/Files/kronos_bars.csv" \
    --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24 --eval_horizon 8 --threshold_pct 0.15 \
    --volume 0.01 --contract_size 100 \
    --duration_min 1440 --flatten_on_exit
```

Ở chế độ này **không cần `--spread`**: spread, slippage, commission và phí qua
đêm đã nằm trong lãi lỗ từng deal mà sàn báo về.

Tín hiệu gửi đi là **vị thế mong muốn** (+1 long, −1 short, 0 flat), không phải
"lệnh". EA kéo tài khoản về đúng trạng thái đó, nên một tín hiệu bị đọc 2 lần
hay đọc lại sau khi khởi động lại cũng không bao giờ nhân đôi vị thế.

Chốt an toàn hai lớp: EA từ chối tài khoản **real** trừ khi bật `InpAllowReal`,
*và* phía Python cũng từ chối trừ khi có `--allow_real`. EA còn chặn khối lượng
vượt `InpMaxVolume`, bỏ qua tín hiệu cũ quá `InpMaxSignalAgeSec`, và chỉ động
vào vị thế mang đúng `InpMagic` của nó.

**(b) Chỉ mô phỏng lệnh, giá vẫn thật** — `--broker paper --feed file`. Nhanh
hơn để bắt đầu (không cần EA đặt lệnh, không cần bật AutoTrading), và vì phần
dự báo giống hệt, nó đủ để **đo chất lượng mô hình**:

```bash
python -m mt5_backtest.live_demo --broker paper --feed file \
    --bars_csv "$COMMON/Files/kronos_bars.csv" \
    --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24 --eval_horizon 8 --threshold_pct 0.15 \
    --volume 0.01 --contract_size 100 --spread 0.30 \
    --duration_min 1440 --flatten_on_exit
```

**Chưa cài MT5?** Chạy ngay bằng dữ liệu Yahoo, không cần tài khoản nào:

```bash
python -m mt5_backtest.live_demo --broker paper --feed yahoo \
    --yahoo_ticker GC=F --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24 --eval_horizon 8 --threshold_pct 0.15 \
    --volume 0.01 --contract_size 100 --spread 0.30 \
    --duration_min 480 --flatten_on_exit
```

Dữ liệu Yahoo trễ ~10–15 phút và không có spread của sàn, nên nó đo *chất lượng
dự báo* chứ không phải điều kiện khớp lệnh. Dùng cầu nối file khi đã có MT5.

### Trên Windows

```bash
# 1) Dry-run hoàn toàn offline trên CSV:
python -m mt5_backtest.live_demo --broker paper --feed replay \
    --csv xauusd_m15.csv --symbol XAUUSD --timeframe M15 \
    --lookback 400 --pred_len 24 --eval_horizon 8 --threshold_pct 0.1 \
    --volume 0.01 --contract_size 100 --spread 0.30

# 2) Paper-trade trên dữ liệu LIVE (dữ liệu thật, lệnh mô phỏng):
python -m mt5_backtest.live_demo --broker paper --feed mt5 \
    --symbol XAUUSD --timeframe M15 --spread 0.30 --max_bars 96

# 3) Gửi lệnh thật lên tài khoản DEMO:
python -m mt5_backtest.live_demo --broker mt5 --feed mt5 \
    --symbol XAUUSD --timeframe M15 --volume 0.01 --max_bars 96 \
    --flatten_on_exit \
    --mt5_login 1234567 --mt5_password "DEMO_PASS" --mt5_server "YourBroker-Demo"
```

### Tham số đáng chú ý

* `--spread` — chênh lệch mua/bán tính bằng **đơn vị giá** (XAUUSD demo thường
  0.15–0.40). Mỗi lệnh khớp lệch nửa spread theo hướng bất lợi, nên một vòng
  mua-bán tốn đúng một spread. **Để mặc định 0 là tự lừa mình:** ở khung M15,
  spread thường ăn hết phần lớn lợi thế. Lấy đúng con số của sàn bạn dùng.
* `--eval_horizon` — nến dùng để ra tín hiệu. `--threshold_pct` — ngưỡng % tối
  thiểu mới vào lệnh (nhỏ hơn ⇒ giao dịch nhiều hơn).
* `--volume` khối lượng mỗi lệnh, `--contract_size` quy đổi lãi lỗ (XAUUSD: 100
  oz/lot).
* `--max_bars` / `--duration_min` điều kiện dừng, `--flatten_on_exit` đóng lệnh
  khi dừng. Dừng bất cứ lúc nào bằng `Ctrl+C` — mọi file log đã ghi tới đó vẫn
  đọc được.

### Kết quả: báo cáo lãi/lỗ

Mỗi phiên ghi ra `results/` 4 file cùng tiền tố:

* **`..._report.html` — mở file này.** Đường vốn theo thời gian thật, từng lệnh
  lãi/lỗ, bảng chi tiết mọi lệnh, nhật ký quyết định từng nến, và phần giải
  nghĩa. Quan trọng nhất: nó nói thẳng lãi hiện tại **có phân biệt được với may
  rủi hay không** (bootstrap khoảng tin cậy + p-value), so sánh với mua-và-giữ,
  và cảnh báo nếu bạn quên khai báo spread.
* `..._trades.csv` — mỗi nến một dòng: hướng, hành động, giá khớp, equity.
* `..._fills.csv` — mỗi lệnh khớp một dòng, kèm lãi/lỗ đã chốt.
* `..._session.json` — cấu hình + tổng kết phiên.

Dựng lại trang HTML bất cứ lúc nào (kể cả khi phiên **đang chạy** hoặc đã bị
tắt giữa chừng):

```bash
python -m mt5_backtest.live_report mt5_backtest/results/XAUUSD_M15_paper_file_..._session.json
```

### Cần chạy bao lâu mới đủ kết luận?

Dưới ~20 lệnh thì con số nào cũng gần như vô nghĩa — một lệnh may mắn đủ đảo
ngược kết quả, và báo cáo sẽ nói đúng như vậy. Nhắm tới **ít nhất 30–50 lệnh đã
đóng**. Với XAUUSD M15 và `--threshold_pct 0.15`, thực tế khoảng 1 lệnh mỗi
2–3 giờ giao dịch, tức cỡ **1–2 tuần** chạy liên tục. Hạ `--threshold_pct` thì
vào lệnh dày hơn nhưng tín hiệu nhiễu hơn.

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

### Cài indicator / EA (một lần)

1. Mở **MetaEditor** (nút trên MT5, hoặc F4).
2. Copy file vào **đúng thư mục** — indicator và EA nằm ở hai chỗ khác nhau:

   | File | Thư mục | Việc của nó |
   |---|---|---|
   | `KronosForecast.mq5` | `MQL5/Indicators/` | vẽ dự báo lên chart |
   | `KronosExport.mq5` | `MQL5/Indicators/` | xuất nến ra CSV cho Python |
   | `KronosTrader.mq5` | `MQL5/Experts/` | **đặt lệnh** theo tín hiệu của Python |

   Trong MetaEditor: chuột phải *Indicators* (hoặc *Expert Advisors*) →
   *Open Folder*.
3. Chọn từng file và bấm **Compile** (F7) — ra file `.ex5` tương ứng.
4. Riêng `KronosTrader.mq5`: kéo từ *Navigator → Expert Advisors* vào chart, và
   **bật nút AutoTrading** trên thanh công cụ. Nếu AutoTrading tắt, EA báo lại
   qua file status và phía Python sẽ dừng ngay với thông báo rõ ràng thay vì
   chạy câm.

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

**Chưa cài MT5 trên Mac?** Tải dữ liệu vàng miễn phí về CSV để test ngay (không
cần MT5), qua Yahoo Finance:

```bash
pip install yfinance
python -m mt5_backtest.fetch_gold_csv --ticker GC=F --interval 15m --period 60d --out gold_m15.csv
python -m mt5_backtest.backtest --source csv --csv gold_m15.csv \
    --symbol XAUUSD --timeframe M15 --lookback 400 --pred_len 24 --n_windows 50
```
(`GC=F` là hợp đồng tương lai vàng COMEX — xấp xỉ XAUUSD giao ngay; Yahoo giới
hạn khung 15m ~60 ngày gần nhất.)

### Forward-test đo lãi/lỗ trên Mac

Cùng cầu nối file đó cũng chạy được vòng forward-test: giá live từ tài khoản
demo, lệnh mô phỏng, báo cáo lãi/lỗ đầy đủ. Xem mục 6 (`--feed file`).

```bash
python -m mt5_backtest.live_demo --broker paper --feed file \
    --bars_csv "$COMMON/kronos_bars.csv" --symbol XAUUSD --timeframe M15 \
    --spread 0.30 --duration_min 1440 --flatten_on_exit
```

### Còn lại gì?

Trên Mac giờ chạy được **cả bốn**: vẽ dự báo, backtest, forward-test mô phỏng,
và **đặt lệnh thật trên tài khoản demo** — tất cả qua cầu nối file, Kronos vẫn
chạy native với MPS.

Thứ duy nhất không chạy được native là `--broker mt5` / `--feed mt5`, vì chúng
gọi thẳng package Python Windows-only. Nếu bạn muốn đúng hai cờ đó thì cần
**Windows VM/VPS**; nhưng `--broker file --feed file` làm đúng việc tương
đương, với lệnh thật trong cùng terminal.
