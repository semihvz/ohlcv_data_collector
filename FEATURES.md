# Desen Cikarici Feature Spesifikasyonu

Bu dosya, mevcut DuckDB verisinden desen cikarici sistem icin uretilecek
ozellikleri ve her ozelligin nasil hesaplanacagini tanimlar.

## Kapsam

Feature satiri, tek bir trade sonucu icin uretilir:

- Basarili trade kaynagi: `successful_trades` + `successful_trade_context_ohlc`
- Basarisiz trade kaynagi: `failed_trades` + `failed_trade_context_ohlc`
- Ana kimlik: `trade_uid`
- Etiket: `label_success = 1` basarili trade, `label_success = 0` basarisiz trade

Her feature sadece trade entry aninda bilinen mumlardan uretilir. Sistem entry'yi
entry mumunun kapanisinda yaptigi icin `bar_offset_from_entry = 0` olan entry
mumu kullanilabilir. Exit sonrasi hicbir veri feature'a karismamalidir.

Ilk surumde sadece tam 100 bar context'e sahip trade'ler kullanilmalidir.
Eksik context varsa feature satiri atlanmalidir.

## Genel Notasyon

Context barlari entry'ye gore siralanir:

- `t = 0`: entry mumu
- `t = -1`: entry'den onceki mum
- `t = -99`: 100 barlik context'in ilk mumu

Kullanilan temel seriler:

- `open[t]`
- `high[t]`
- `low[t]`
- `close[t]`
- `volume[t]`
- `quote_volume[t]`
- `trade_count[t]`
- `taker_buy_base[t]`
- `taker_buy_quote[t]`

Fiyat ve hacim verileri DuckDB'de integer scale ile saklanir. Oran ve yuzde
hesaplarinda integer degerler dogrudan kullanilabilir; scale carpani bolme
islemlerinde sadelesir.

Yuzdelik/basis point donusumlerinde:

```text
bp(x) = 10000 * x
log_return(a, b) = ln(a / b)
```

Sifira bolme, bos pencere veya yetersiz veri durumunda feature degeri `NULL`
olmalidir.

## Yon Normalizasyonu

LONG ve SHORT trade'leri ayni feature diline cevirmek icin her trade'e yon
katsayisi verilir:

```text
direction = +1  if side = LONG
direction = -1  if side = SHORT
```

Yonlu fiyat hareketi:

```text
directional_return(a, b) = direction * 10000 * ln(a / b)
```

Bu sekilde pozitif deger her zaman trade yonu lehine hareket anlamina gelir.

Mum ici ekstrem tanimlari:

```text
favorable_extreme[t] =
  high[t] if LONG
  low[t]  if SHORT

adverse_extreme[t] =
  low[t]  if LONG
  high[t] if SHORT
```

Mum ici kapanis konumu:

```text
candle_close_position[t] =
  (close[t] - low[t]) / (high[t] - low[t])       if LONG
  (high[t] - close[t]) / (high[t] - low[t])      if SHORT
```

Bu deger `1`e yaklastikca mum trade yonu lehine kapanmistir.

## Pencere Setleri

Standart pencereler:

```text
W_SHORT = [3, 5, 10]
W_MAIN  = [5, 10, 20, 50]
W_ALL   = [3, 5, 10, 20, 50, 100]
```

100 barlik context entry mumunu da icerir. Bu nedenle `ret_100`, `close[0]`
ile `close[-99]` arasinda hesaplanir.

## 1. Trend ve Momentum

### `ret_1`

Entry mumunun bir onceki muma gore yonlu getirisi.

```text
ret_1 = direction * 10000 * ln(close[0] / close[-1])
```

### `ret_3`, `ret_5`, `ret_10`, `ret_20`, `ret_50`, `ret_100`

Son pencere boyunca yonlu getiri.

```text
ret_W = direction * 10000 * ln(close[0] / close[-W])
```

`W = 100` icin `close[-99]` kullanilir.

### `close_slope_10`, `close_slope_20`, `close_slope_50`

Son `W` mumda yon normalize edilmis log close serisinin OLS egimi.

```text
y[i] = direction * ln(close[i] / close[0])
x[i] = bar_index
close_slope_W = 10000 * OLS_slope(y over last W bars)
```

Pozitif egim, trade yonune dogru trend anlamina gelir.

### `ema_distance_10`, `ema_distance_20`, `ema_distance_50`

Entry kapanisinin EMA'ya yonlu uzakligi.

```text
ema_distance_W = direction * 10000 * ln(close[0] / EMA(close, W)[0])
```

### `sma_distance_10`, `sma_distance_20`, `sma_distance_50`

Entry kapanisinin SMA'ya yonlu uzakligi.

```text
sma_distance_W = direction * 10000 * ln(close[0] / SMA(close, W)[0])
```

### `consecutive_favorable_closes`

Entry'den geriye dogru kesintisiz trade yonu lehine kapanis sayisi.

```text
count backward while direction * (close[t] - close[t-1]) > 0
```

### `consecutive_adverse_closes`

Entry'den geriye dogru kesintisiz trade yonu aleyhine kapanis sayisi.

```text
count backward while direction * (close[t] - close[t-1]) < 0
```

### `favorable_candle_count_5`, `favorable_candle_count_10`, `favorable_candle_count_20`

Son `W` mum icinde govdesi trade yonu lehine olan mum sayisi.

```text
favorable_candle_count_W =
  count(direction * (close[t] - open[t]) > 0 over last W bars)
```

## 2. Volatilite ve Sikisma

### `atr_pct_14`

Son 14 mumun average true range degeri, entry kapanisina bolunmus halde.

```text
true_range[t] = max(
  high[t] - low[t],
  abs(high[t] - close[t-1]),
  abs(low[t] - close[t-1])
)

atr_pct_14 = 10000 * mean(true_range over last 14 bars) / close[0]
```

### `range_pct_5`, `range_pct_10`, `range_pct_20`, `range_pct_50`

Pencere icindeki toplam high-low alani.

```text
range_pct_W = 10000 * (max(high over W) - min(low over W)) / close[0]
```

### `body_range_ratio_5`, `body_range_ratio_10`

Pencere icinde mum govdelerinin toplam range'e orani.

```text
body_range_ratio_W =
  sum(abs(close[t] - open[t]) over W) /
  sum(high[t] - low[t] over W)
```

### `volatility_ratio_5_20`

Kisa volatilitenin orta volatiliteye orani.

```text
volatility_ratio_5_20 =
  mean(true_range over last 5 bars) /
  mean(true_range over last 20 bars)
```

### `volatility_ratio_10_50`

```text
volatility_ratio_10_50 =
  mean(true_range over last 10 bars) /
  mean(true_range over last 50 bars)
```

### `volatility_compression_20_100`

Son 20 mumluk alanin 100 mumluk alana orani.

```text
volatility_compression_20_100 =
  range_pct_20 / range_pct_100
```

`range_pct_100`, 100 barlik context'in tamamindan hesaplanir.

### `range_expansion_last_3`, `range_expansion_last_5`

Son `N` mum range ortalamasinin onceki 20 mum range ortalamasina orani.

```text
range_expansion_last_N =
  mean(high[t] - low[t] over last N bars) /
  mean(high[t] - low[t] over bars [-N-20, -N-1])
```

## 3. Mum Yapisi

Tek mum icin temel oranlar:

```text
candle_range[t] = high[t] - low[t]
body_pct[t] = abs(close[t] - open[t]) / candle_range[t]
upper_wick_pct[t] = (high[t] - max(open[t], close[t])) / candle_range[t]
lower_wick_pct[t] = (min(open[t], close[t]) - low[t]) / candle_range[t]
```

### `last_body_pct`

```text
last_body_pct = body_pct[0]
```

### `last_upper_wick_pct`

```text
last_upper_wick_pct = upper_wick_pct[0]
```

### `last_lower_wick_pct`

```text
last_lower_wick_pct = lower_wick_pct[0]
```

### `last_directional_wick_pct`

Trade yonundeki fitil orani.

```text
last_directional_wick_pct =
  upper_wick_pct[0] if LONG
  lower_wick_pct[0] if SHORT
```

### `last_adverse_wick_pct`

Trade yonunun tersindeki fitil orani.

```text
last_adverse_wick_pct =
  lower_wick_pct[0] if LONG
  upper_wick_pct[0] if SHORT
```

### `avg_body_pct_3`, `avg_body_pct_5`

```text
avg_body_pct_W = mean(body_pct[t] over last W bars)
```

### `avg_wick_pct_3`, `avg_wick_pct_5`

Iki fitilin ortalama toplam orani.

```text
avg_wick_pct_W = mean(upper_wick_pct[t] + lower_wick_pct[t] over last W bars)
```

### `large_body_count_5`

Son 5 mumda govde orani buyuk mum sayisi.

```text
large_body_count_5 = count(body_pct[t] >= 0.60 over last 5 bars)
```

### `doji_count_10`

Son 10 mumda doji benzeri mum sayisi.

```text
doji_count_10 = count(body_pct[t] <= 0.10 over last 10 bars)
```

### `strong_close_count_5`

Son 5 mumda trade yonu lehine guclu kapanis sayisi.

```text
strong_close_count_5 =
  count(candle_close_position[t] >= 0.75 over last 5 bars)
```

## 4. Range Icindeki Konum

### `close_position_20`, `close_position_50`, `close_position_100`

Entry kapanisinin pencere range'i icindeki yon normalize konumu.

```text
rolling_high_W = max(high over W)
rolling_low_W = min(low over W)

close_position_W =
  (close[0] - rolling_low_W) / (rolling_high_W - rolling_low_W)   if LONG
  (rolling_high_W - close[0]) / (rolling_high_W - rolling_low_W)  if SHORT
```

`1`e yakin deger, entry'nin trade yonundeki range ucuna yakin oldugunu gosterir.

### `distance_to_high_20`, `distance_to_high_50`, `distance_to_high_100`

Entry kapanisinin pencere high'ina ham mesafesi.

```text
distance_to_high_W = 10000 * (rolling_high_W - close[0]) / close[0]
```

### `distance_to_low_20`, `distance_to_low_50`, `distance_to_low_100`

Entry kapanisinin pencere low'una ham mesafesi.

```text
distance_to_low_W = 10000 * (close[0] - rolling_low_W) / close[0]
```

### `distance_to_favorable_extreme_20`, `distance_to_favorable_extreme_50`, `distance_to_favorable_extreme_100`

Trade yonundeki pencere ekstremine mesafe.

```text
distance_to_favorable_extreme_W =
  distance_to_high_W if LONG
  distance_to_low_W  if SHORT
```

### `distance_to_adverse_extreme_20`, `distance_to_adverse_extreme_50`, `distance_to_adverse_extreme_100`

Trade yonunun tersindeki pencere ekstremine mesafe.

```text
distance_to_adverse_extreme_W =
  distance_to_low_W  if LONG
  distance_to_high_W if SHORT
```

### `bars_since_high_20`, `bars_since_high_100`

Pencere icindeki son high ekstreminden entry'ye kadar gecen mum sayisi.

```text
bars_since_high_W = 0 - offset_of_latest_max(high over W)
```

### `bars_since_low_20`, `bars_since_low_100`

Pencere icindeki son low ekstreminden entry'ye kadar gecen mum sayisi.

```text
bars_since_low_W = 0 - offset_of_latest_min(low over W)
```

### `bars_since_favorable_extreme_20`, `bars_since_favorable_extreme_100`

```text
bars_since_favorable_extreme_W =
  bars_since_high_W if LONG
  bars_since_low_W  if SHORT
```

### `bars_since_adverse_extreme_20`, `bars_since_adverse_extreme_100`

```text
bars_since_adverse_extreme_W =
  bars_since_low_W  if LONG
  bars_since_high_W if SHORT
```

## 5. Breakout ve Pullback

Breakout hesaplarinda entry mumu, onceki range'e gore degerlendirilmelidir.
Bu nedenle `prior_high_W` ve `prior_low_W`, entry mumu haric son `W` mumdan
hesaplanir.

```text
prior_high_W = max(high over bars [-W, -1])
prior_low_W = min(low over bars [-W, -1])
```

### `new_high_20`, `new_high_50`

Entry kapanisi onceki pencere high'inin uzerinde mi?

```text
new_high_W = close[0] > prior_high_W
```

### `new_low_20`, `new_low_50`

Entry kapanisi onceki pencere low'unun altinda mi?

```text
new_low_W = close[0] < prior_low_W
```

### `new_favorable_extreme_20`, `new_favorable_extreme_50`

Trade yonunde yeni ekstrem kirilimi.

```text
new_favorable_extreme_W =
  new_high_W if LONG
  new_low_W  if SHORT
```

### `new_adverse_extreme_20`, `new_adverse_extreme_50`

Trade yonunun tersinde yeni ekstrem kirilimi.

```text
new_adverse_extreme_W =
  new_low_W  if LONG
  new_high_W if SHORT
```

### `breakout_strength_20`, `breakout_strength_50`

Trade yonundeki breakout gucu, ATR ile normalize edilir.

```text
breakout_strength_W =
  (close[0] - prior_high_W) / ATR_14 if LONG
  (prior_low_W - close[0]) / ATR_14  if SHORT
```

Pozitif deger, trade yonunde breakout oldugunu gosterir.

### `pullback_from_high_20`, `pullback_from_high_50`

Ham olarak pencere high'ina geri cekilme mesafesi.

```text
pullback_from_high_W = 10000 * (rolling_high_W - close[0]) / close[0]
```

### `pullback_from_favorable_extreme_20`, `pullback_from_favorable_extreme_50`

Trade yonundeki son ekstremden entry kapanisina geri cekilme mesafesi.

```text
pullback_from_favorable_extreme_W =
  distance_to_high_W if LONG
  distance_to_low_W  if SHORT
```

### `pullback_depth_10`

Son 10 mum icinde trade yonu lehine en iyi kapanistan sonra gelen en derin
aleyhte kapanis mesafesi.

```text
directional_close[t] = direction * ln(close[t] / close[0])

pullback_depth_10 =
  10000 * max_peak_to_trough_drawdown(directional_close over last 10 bars)
```

### `pullback_recovery_5`

Son 10 mumdaki en kotu yonlu kapanistan entry kapanisina toparlanma.

```text
worst_directional_close_10 = min(directional_close over last 10 bars)
pullback_recovery_5 =
  10000 * (directional_close[0] - worst_directional_close_10)
```

## 6. Hacim

### `volume_ratio_3_20`

```text
volume_ratio_3_20 = mean(volume over last 3 bars) / mean(volume over last 20 bars)
```

### `volume_ratio_5_50`

```text
volume_ratio_5_50 = mean(volume over last 5 bars) / mean(volume over last 50 bars)
```

### `volume_zscore_5`

Son 5 mum ortalama hacminin 100 mumluk hacim dagilimina gore z-score'u.

```text
volume_zscore_5 =
  (mean(volume over last 5 bars) - mean(volume over last 100 bars)) /
  stddev(volume over last 100 bars)
```

### `volume_zscore_20`

```text
volume_zscore_20 =
  (mean(volume over last 20 bars) - mean(volume over last 100 bars)) /
  stddev(volume over last 100 bars)
```

### `quote_volume_zscore_20`

```text
quote_volume_zscore_20 =
  (mean(quote_volume over last 20 bars) - mean(quote_volume over last 100 bars)) /
  stddev(quote_volume over last 100 bars)
```

### `volume_slope_10`, `volume_slope_20`

Log hacim serisinin OLS egimi.

```text
volume_slope_W = OLS_slope(ln(volume[t]) over last W bars)
```

### `volume_spike_last_1`

Entry mum hacminin son 20 mum ortalamasina orani.

```text
volume_spike_last_1 = volume[0] / mean(volume over last 20 bars)
```

### `volume_spike_last_3`

Son 3 mumdaki maksimum hacmin son 20 mum ortalamasina orani.

```text
volume_spike_last_3 = max(volume over last 3 bars) / mean(volume over last 20 bars)
```

### `volume_dryup_10`

Son 10 mum ortalama hacminin son 50 mum ortalamasina orani.

```text
volume_dryup_10 = mean(volume over last 10 bars) / mean(volume over last 50 bars)
```

Dusuk deger hacim kurumasini gosterir.

## 7. Taker Buy ve Agresif Emir Baskisi

Taker buy ratio, mum icindeki agresif alici payidir:

```text
taker_buy_ratio[t] = taker_buy_base[t] / volume[t]
```

Pencere icin hacim agirlikli hesaplanir:

```text
taker_buy_ratio_W = sum(taker_buy_base over W) / sum(volume over W)
```

### `taker_buy_ratio_last`

```text
taker_buy_ratio_last = taker_buy_base[0] / volume[0]
```

### `taker_buy_ratio_3`, `taker_buy_ratio_5`, `taker_buy_ratio_20`

```text
taker_buy_ratio_W = sum(taker_buy_base over W) / sum(volume over W)
```

### `directional_taker_pressure_3`, `directional_taker_pressure_5`, `directional_taker_pressure_20`

Trade yonune gore agresif emir baskisi.

```text
directional_taker_pressure_W =
  direction * (2 * taker_buy_ratio_W - 1)
```

LONG icin alici baskisi pozitif, SHORT icin satici baskisi pozitif olur.

### `taker_pressure_zscore_20`

Son 20 mumluk yonlu taker baskisinin 100 mumluk dagilima gore z-score'u.

```text
directional_pressure[t] = direction * (2 * taker_buy_ratio[t] - 1)

taker_pressure_zscore_20 =
  (mean(directional_pressure over last 20 bars) -
   mean(directional_pressure over last 100 bars)) /
  stddev(directional_pressure over last 100 bars)
```

## 8. Islem Sayisi ve Likidite

### `trade_count_zscore_20`

```text
trade_count_zscore_20 =
  (mean(trade_count over last 20 bars) - mean(trade_count over last 100 bars)) /
  stddev(trade_count over last 100 bars)
```

### `trade_count_ratio_5_50`

```text
trade_count_ratio_5_50 =
  mean(trade_count over last 5 bars) /
  mean(trade_count over last 50 bars)
```

### `avg_trade_size_20`

Son 20 mumdaki ortalama islem buyuklugu. Quote volume kullanilir.

```text
avg_trade_size_20 =
  sum(quote_volume over last 20 bars) /
  sum(trade_count over last 20 bars)
```

### `avg_trade_size_zscore_20`

Son 20 mum ortalama islem buyuklugunun 100 mumluk dagilima gore z-score'u.
Once her mum icin `quote_volume[t] / trade_count[t]` hesaplanir.

```text
avg_trade_size_zscore_20 =
  (mean(avg_trade_size_per_bar over last 20 bars) -
   mean(avg_trade_size_per_bar over last 100 bars)) /
  stddev(avg_trade_size_per_bar over last 100 bars)
```

### `liquidity_change_10`

Son 10 mum quote volume'unun son 50 mum quote volume'una orani.

```text
liquidity_change_10 =
  mean(quote_volume over last 10 bars) /
  mean(quote_volume over last 50 bars)
```

## 9. Rejim Ozellikleri

### `trend_strength_20`, `trend_strength_50`

Yonlu trend gucunun toplam range'e orani.

```text
trend_strength_W = abs(ret_W) / range_pct_W
```

Deger `1`e yaklastikca hareket daha tek yonlu, `0`a yaklastikca daha yataydir.

### `choppiness_20`, `choppiness_50`

Klasik choppiness index.

```text
choppiness_W =
  100 * log10(sum(true_range over W) / (max(high over W) - min(low over W))) /
  log10(W)
```

Yuksek deger yatay ve dalgali rejimi, dusuk deger trend rejimini gosterir.

### `volatility_regime_100`

Mevcut ATR'nin 100 mumluk true range ortalamasina orani.

```text
volatility_regime_100 =
  mean(true_range over last 14 bars) /
  mean(true_range over last 100 bars)
```

### `volume_regime_100`

Son 20 mum hacminin 100 mum hacmine orani.

```text
volume_regime_100 =
  mean(volume over last 20 bars) /
  mean(volume over last 100 bars)
```

### `range_regime_100`

Son 20 mum range'inin 100 mum range'ine orani.

```text
range_regime_100 = range_pct_20 / range_pct_100
```

## Cikarilacak Feature Listesi

Ilk surumde uretilecek feature kolonlari:

```text
ret_1
ret_3
ret_5
ret_10
ret_20
ret_50
ret_100
close_slope_10
close_slope_20
close_slope_50
ema_distance_10
ema_distance_20
ema_distance_50
sma_distance_10
sma_distance_20
sma_distance_50
consecutive_favorable_closes
consecutive_adverse_closes
favorable_candle_count_5
favorable_candle_count_10
favorable_candle_count_20
atr_pct_14
range_pct_5
range_pct_10
range_pct_20
range_pct_50
range_pct_100
body_range_ratio_5
body_range_ratio_10
volatility_ratio_5_20
volatility_ratio_10_50
volatility_compression_20_100
range_expansion_last_3
range_expansion_last_5
last_body_pct
last_upper_wick_pct
last_lower_wick_pct
last_directional_wick_pct
last_adverse_wick_pct
avg_body_pct_3
avg_body_pct_5
avg_wick_pct_3
avg_wick_pct_5
large_body_count_5
doji_count_10
strong_close_count_5
close_position_20
close_position_50
close_position_100
distance_to_high_20
distance_to_high_50
distance_to_high_100
distance_to_low_20
distance_to_low_50
distance_to_low_100
distance_to_favorable_extreme_20
distance_to_favorable_extreme_50
distance_to_favorable_extreme_100
distance_to_adverse_extreme_20
distance_to_adverse_extreme_50
distance_to_adverse_extreme_100
bars_since_high_20
bars_since_high_100
bars_since_low_20
bars_since_low_100
bars_since_favorable_extreme_20
bars_since_favorable_extreme_100
bars_since_adverse_extreme_20
bars_since_adverse_extreme_100
new_high_20
new_high_50
new_low_20
new_low_50
new_favorable_extreme_20
new_favorable_extreme_50
new_adverse_extreme_20
new_adverse_extreme_50
breakout_strength_20
breakout_strength_50
pullback_from_high_20
pullback_from_high_50
pullback_from_favorable_extreme_20
pullback_from_favorable_extreme_50
pullback_depth_10
pullback_recovery_5
volume_ratio_3_20
volume_ratio_5_50
volume_zscore_5
volume_zscore_20
quote_volume_zscore_20
volume_slope_10
volume_slope_20
volume_spike_last_1
volume_spike_last_3
volume_dryup_10
taker_buy_ratio_last
taker_buy_ratio_3
taker_buy_ratio_5
taker_buy_ratio_20
directional_taker_pressure_3
directional_taker_pressure_5
directional_taker_pressure_20
taker_pressure_zscore_20
trade_count_zscore_20
trade_count_ratio_5_50
avg_trade_size_20
avg_trade_size_zscore_20
liquidity_change_10
trend_strength_20
trend_strength_50
choppiness_20
choppiness_50
volatility_regime_100
volume_regime_100
range_regime_100
```

## Onerilen `trade_features` Kolonlari

Feature tablosu icin minimum kolon seti:

```text
trade_uid
symbol
side
entry_open_time_ms
entry_time_ms
label_success
outcome
attempt_no
<feature kolonlari>
created_at_ms
```

`outcome` analitik kontrol icin tutulabilir, ancak pattern mining hedefi olarak
yalnizca `label_success` kullanilmalidir.

## Extraction Akisi

1. `successful_trades` ve `failed_trades` tablolarindan trade metadatasini oku.
2. Ilgili context view'dan `trade_uid` bazinda 100 OHLCV barini oku.
3. Barlari `open_time_ms` artan sirada diz.
4. Context tam 100 bar degilse trade'i atla.
5. `side` alanina gore yon normalizasyonu uygula.
6. Bu dosyadaki feature formullerini sadece `t <= 0` barlariyla hesapla.
7. `label_success` alanini trade'in geldigi tabloya gore set et.
8. Feature satirini `trade_features` tablosuna yaz.

## Leakage Yasaklari

Asagidaki alanlar feature hesaplamasinda kullanilmayacak:

- `exit_index`
- `exit_time_ms`
- `exit_price_i`
- `outcome`
- `pnl_bp`
- `bars_held`
- `max_favorable_bp`
- `max_adverse_bp`
- TP/SL sonrasi herhangi bir mum

Bu alanlar sadece etiketleme, raporlama veya model performansi analizinde
kullanilabilir.
