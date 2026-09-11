# ⚡ LLM Endpoint Watchdog

OpenAI uyumlu LLM endpointlerini izleyen, hafif ve gerçek zamanlı bir web paneli. Her endpoint'in `/v1/models` adresini kontrol eder; erişilebilirlik, model listesi, yanıt süresi ve uptime bilgisini gösterir. vLLM, Ollama, LocalAI ve aynı API biçimini sunan diğer servislerle kullanılabilir.

Harici Python paketi gerektirmez.

## Ekran görüntüsü

![LLM Endpoint Watchdog paneli](screenshots/llm-endpoint-watchdog.png)

## Çalıştırma

Gereksinim: Python 3.9 veya daha yeni bir sürüm.

```bash
git clone https://github.com/Eormeci/llm-endpoint-watchdog.git
cd llm-endpoint-watchdog
python3 llm-endpoint-watchdog.py
```

Ardından tarayıcıda [http://localhost:9090](http://localhost:9090) adresini açın.

Panel ilk açılışta `127.0.0.1:8000` adresini izler. Başka bir OpenAI uyumlu LLM servisi eklemek için paneldeki alana `sunucu-ip:port` biçiminde adres girin:

```text
10.0.0.5:8000
```

Eklediğiniz endpointler yerel `watchdog_endpoints.json` dosyasına kaydedilir. Bu dosya Git tarafından takip edilmez.

Uygulamayı durdurmak için terminalde `Ctrl+C` tuşlarına basın.

## Ayarlar

Kontrol aralığı, panel portu ve zaman aşımı `llm-endpoint-watchdog.py` dosyasının başındaki sabitlerden değiştirilebilir:

```python
CHECK_INTERVAL = 10
DASHBOARD_PORT = 9090
TIMEOUT = 5
```
