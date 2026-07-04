# Sequence Prediction Kutuphanesi Tasarimi

Bu dokuman, dilde "baglama gore sonraki kelime/token tahmini" yapan ve ayni
mantigi trade verisine uygulayabilen genel amacli bir sequence prediction
kutuphanesi icin tasarim notlarini tanimlar.

Ana fikir:

```text
gecmis baglam -> siradaki token/state olasiligi
P(next | context)
```

Kutuphanenin cekirdegi dil ve trade icin ayni kalmalidir. Degisen kisim
tokenizer, dataset hazirlama ve degerlendirme metrikleridir.

## Hedef

Ilk hedef buyuk bir LLM egitmek degil, genisletilebilir bir sequence prediction
altyapisi kurmaktir.

Bu altyapi sunlari desteklemelidir:

- Metinden next-token prediction
- Trade context'inden sonraki market state tahmini
- Trade context'inden TP/SL olasiligi tahmini
- Farkli tokenizer ve model mimarilerini ayni trainer ile egitme
- Dil ve trade icin ayri evaluation metrikleri

## Ust Seviye Mimari

Kutuphaneyi bes ana parcaya ayirmak gerekir:

```text
1. Tokenizer
2. Dataset Builder
3. Model
4. Trainer
5. Inference Engine
```

### 1. Tokenizer

Ham veriyi modelin gorecegi token dizisine cevirir.

Dil icin:

- Character tokenizer
- Word tokenizer
- BPE/subword tokenizer

Trade icin:

- Feature-bin tokenizer
- Market-state tokenizer
- Multi-token-per-candle tokenizer

### 2. Dataset Builder

Uzun token dizilerini egitim orneklerine cevirir.

Gorevleri:

- Sliding window context uretmek
- Input/target shift hazirlamak
- Train/validation/test ayrimi yapmak
- Batch uretmek
- Zaman serisi icin leakage engellemek

Dil icin klasik next-token hedefi:

```text
input:  token[0:n]
target: token[1:n+1]
```

Trade icin hedef, secilen goreve gore degisir:

```text
input:  last_100_market_states
target: next_market_state
```

veya:

```text
input:  last_100_market_states + side
target: TP_before_SL
```

### 3. Model

Ilk surumde birden fazla seviye desteklenmelidir:

- N-gram / Markov baseline
- LSTM veya GRU
- Kucuk causal Transformer

N-gram/Markov baseline onemlidir; transformer sonucunun gercekten deger
kattigini gostermek icin karsilastirma noktasi verir.

### 4. Trainer

Model egitimini yonetir.

Desteklemesi gerekenler:

- Cross entropy loss
- Checkpoint kaydetme/yukleme
- Validation loss takibi
- Early stopping
- Learning rate schedule
- Reproducible seed
- Metric logging

### 5. Inference Engine

Egitilmis model ile tahmin uretir.

Dil icin:

- Top-k sampling
- Top-p sampling
- Temperature
- Greedy decode

Trade icin:

- Sonraki market state olasiliklari
- TP/SL olasiligi
- Confidence skoru
- Threshold bazli sinyal uretimi

## Onerilen Klasor Yapisi

```text
sequence_predictor/
  tokenizers/
    char_tokenizer.py
    word_tokenizer.py
    bpe_tokenizer.py
    trade_tokenizer.py

  datasets/
    text_dataset.py
    trade_dataset.py
    windowed_dataset.py

  models/
    ngram.py
    lstm.py
    transformer.py

  training/
    trainer.py
    losses.py
    checkpoint.py

  inference/
    sampler.py
    predictor.py

  evaluation/
    text_metrics.py
    trade_metrics.py
    backtest_metrics.py
```

## Dil Tarafi

Dil modeli su problemi cozer:

```text
"bugun hava cok" -> "guzel" olasiligi yuksek
```

Model hedefi:

```text
P(next_token | previous_tokens)
```

### Tokenizer Secenekleri

#### Character Tokenizer

Avantajlari:

- Uygulamasi en kolaydir.
- Vocabulary kucuktur.
- Bilinmeyen token problemi azdir.

Dezavantajlari:

- Uzun context gerekir.
- Kelime seviyesinde anlam daha gec ogrenilir.

#### Word Tokenizer

Avantajlari:

- Basit ve okunabilir.
- Ilk denemeler icin uygundur.

Dezavantajlari:

- Vocabulary cok buyuyebilir.
- Nadir kelimeler sorun olur.

#### BPE/Subword Tokenizer

Avantajlari:

- Modern LLM mantigina en yakindir.
- Vocabulary kontrol edilebilir.
- Nadir kelimeler alt parcalarla temsil edilir.

Dezavantajlari:

- Uygulamasi daha karmasiktir.

## Trade Tarafi

Trade tarafinda "kelime" yerine "market state token" kullanilir.

Ham fiyatlar dogrudan modele verilmemelidir:

```text
BTC close = 63421.5
ETH close = 3432.1
```

Bunun yerine normalize edilmis ve semboller arasi karsilastirilabilir feature
durumlari kullanilmalidir:

```text
ret_5 = +0.42%
range_position_100 = ust %20
volume_zscore = yuksek
volatility = sikisik
```

Sonra bunlar token/bin haline getirilir:

```text
RET_5_POS_MEDIUM
RANGE_POS_TOP_20
VOLUME_HIGH
VOL_LOW
```

Bu sayede BTC, ETH, SOL veya dusuk fiyatli semboller ayni model diline girer.

## Trade Token Tasarimi

Bir 1m mum tek token olmak zorunda degildir. Daha esnek yapi:

```text
her mum -> birden fazla feature token
```

Ornek sequence:

```text
VOL_COMPRESSED CLOSE_NEAR_HIGH VOLUME_DRY
VOL_COMPRESSED CLOSE_MID VOLUME_DRY
BREAKOUT_UP VOLUME_SPIKE TAKER_BUY_STRONG
```

Ornek token aileleri:

```text
RET_UP_SMALL
RET_UP_MEDIUM
RET_UP_BIG
RET_DOWN_SMALL
RET_DOWN_MEDIUM
RET_DOWN_BIG
VOL_COMPRESSED
VOL_NORMAL
VOL_EXPANDING
CLOSE_NEAR_HIGH
CLOSE_MID
CLOSE_NEAR_LOW
VOLUME_DRY
VOLUME_NORMAL
VOLUME_SPIKE
TAKER_BUY_STRONG
TAKER_SELL_STRONG
BREAKOUT_UP
BREAKOUT_DOWN
PULLBACK_SHALLOW
PULLBACK_DEEP
```

SHORT trade'ler icin tokenlar yon normalize edilebilir. Boylece model,
"trade yonu lehine hareket" kavramini LONG ve SHORT icin ayni sekilde gorur.

## Trade Hedefleri

Uc farkli hedef desteklenebilir.

### 1. Sonraki Market State Tahmini

```text
P(next_market_state | last_100_states)
```

Bu hedef dil modeline en cok benzeyen hedeftir. Model piyasanin siradaki
durumunu tahmin etmeyi ogrenir.

### 2. Trade Sonucu Tahmini

```text
P(TP before SL | last_100_states, side)
```

Bu hedef stratejiye dogrudan baglanir. Ilk trade versiyonu icin en pratik hedef
budur.

Olasilik ciktilari:

```text
P(success)
P(failure)
```

veya cok sinifli:

```text
P(TP)
P(SL)
P(BOTH_HIT_SAME_CANDLE)
P(OPEN)
```

### 3. Cok Adimli State Tahmini

```text
P(next_1, next_2, ..., next_20 states | context)
```

Bu hedef daha karmasiktir ama modelin sadece tek mum degil, yakin gelecek
patikasini tahmin etmesini saglar.

## Ilk Surum Icin Onerilen Yol

Ilk trade surumunde hedef:

```text
context -> TP olasiligi
context -> SL olasiligi
```

Neden:

- Strateji kararina dogrudan baglanir.
- Pattern miner ile birlikte yorumlanabilir.
- Backtest metrikleriyle kolay test edilir.
- Sonraki state tahmininden daha az dolaylidir.

## Model Seviyeleri

### N-gram / Markov Baseline

Ilk baseline model olmalidir.

Girdi:

```text
son N token/state
```

Cikti:

```text
siradaki token/state olasiligi
```

Avantajlari:

- Hizli egitilir.
- Yorumlanabilir.
- Transformer icin karsilastirma noktasi verir.

### LSTM / GRU

Orta seviye modeldir.

Avantajlari:

- Sequence baglamini n-gram'den daha iyi yakalar.
- Transformer'a gore daha hafiftir.

Dezavantajlari:

- Uzun baglamlarda transformer kadar iyi olmayabilir.

### Causal Transformer

Ana hedef mimaridir.

Ozellikleri:

- Masked self-attention
- Positional encoding
- Token embedding
- Feed-forward block
- Layer normalization
- Causal next-token prediction

Kucuk baslanmalidir:

```text
layers: 4-6
heads: 4-8
embedding_dim: 128-512
context_length: 128-512 token
```

Trade tarafinda context token sayisi, bir mumdan kac token uretildigine baglidir.

## Trade Evaluation Kurallari

Trade tarafinda klasik ML accuracy tek basina yeterli degildir.

Bakilmasi gereken metrikler:

```text
accuracy
precision
recall
ROC AUC
PR AUC
calibration
expected value
win rate
average R
max drawdown
sample size
profit factor
walk-forward stability
```

Trade modelinde en onemli soru:

```text
Modelin yuksek confidence dedigi sinyaller pozitif expected value uretiyor mu?
```

## Zaman Ayrimi ve Leakage

Trade dataset'inde random split kullanilmamalidir.

Dogru ayrim:

```text
train: eski donem
validation: daha yeni donem
test: en yeni donem
```

Ornek:

```text
train      2019-2024
validation 2025
test       2026
```

Walk-forward test daha iyidir:

```text
train A -> test B
train A+B -> test C
train A+B+C -> test D
```

Feature veya token uretirken sadece entry aninda bilinen bilgiler
kullanilmalidir. TP/SL sonucu, exit zamani, bars held gibi sonuc alanlari
input'a karismamalidir.

## Pattern Miner ile Iliski

Sequence model ve pattern miner birbirini tamamlar.

Pattern miner:

```text
hangi yapilar basariyla iliskili?
```

Sequence model:

```text
bu baglamdan sonra basari olasiligi nedir?
```

Onerilen nihai akış:

```text
FEATURES.md
  -> trade tokenizer
  -> sequence model
  -> TP/SL probability
  -> pattern/backtest engine
```

Pattern miner aciklanabilir kurallar uretir. Sequence model bu kurallarin
yakalayamadigi karmasik sirali baglamlari ogrenebilir.

## Ilk Gelistirme Plani

1. Kucuk text next-token pipeline kur.
2. Character ve word tokenizer ekle.
3. N-gram baseline model ekle.
4. Kucuk Transformer ekle.
5. `FEATURES.md` tabanli trade tokenizer ekle.
6. Trade sequence dataset builder yaz.
7. Ilk hedef olarak `P(success | context, side)` egit.
8. Zaman bazli validation/test ayir.
9. Backtest metrikleriyle confidence threshold test et.
10. Pattern miner skorlarini model tahminiyle birlestir.

## Baslangic Icin Kritik Kararlar

### Ham fiyat degil normalize state

Trade tarafinda modelin fiyat seviyelerini ezberlemesini engellemek icin ham
close/open/high/low yerine normalize edilmis feature bin'leri kullanilmalidir.

### Once baseline, sonra transformer

Transformer dogrudan kurulabilir ama baseline olmadan deger kattigi anlasilmaz.
Bu nedenle once n-gram/Markov baseline gereklidir.

### Prediction kalitesi ile trade kalitesi ayridir

Sonraki tokeni iyi tahmin eden model her zaman para kazandiran model degildir.
Trade tarafinda nihai karar expected value ve walk-forward stabiliteyle
verilmelidir.

### Confidence threshold sart

Model her durumda islem acmamalidir. Sadece yeterince yuksek confidence ve
pozitif expected value olan durumlar sinyal sayilmalidir.

Ornek:

```text
if P(success) >= 0.62 and sample_regime_is_valid:
    allow_trade
else:
    skip
```

## Onerilen Ilk MVP

Ilk MVP kapsami:

- Text icin word-level next-token modeli
- Trade icin feature-bin tokenizer
- N-gram baseline
- Kucuk causal Transformer
- `P(success)` cikisi
- Zaman bazli train/validation/test
- Confidence threshold raporu
- CSV/JSON tahmin export'u

MVP'nin amaci buyuk ve pahali model egitmek degil, ayni cekirdegin hem dil hem
trade sequence verisinde calistigini kanitlamaktir.
