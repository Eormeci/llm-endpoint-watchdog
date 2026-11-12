# ⚡ LLM Endpoint Watchdog

OpenAI uyumlu LLM endpointlerini izleyen, hafif ve gerçek zamanlı bir web paneli. Her endpoint'in `/v1/models` adresini kontrol eder; erişilebilirlik, model listesi, yanıt süresi ve uptime bilgisini gösterir. vLLM, Ollama, LocalAI ve aynı API biçimini sunan diğer servislerle kullanılabilir.

Harici Python paketi gerektirmez. Tek dosya, tek komut.

![LLM Endpoint Watchdog paneli](screenshots/llm-endpoint-watchdog.png)

## Neler yapar

- **Endpoint izleme** — her `host:port` için `/v1/models` kontrolü, model listesi, yanıt süresi ve uptime.
- **Codex / Claude kullanımı** — kurulumda hiçbir ayar istemez. Codex CLI ve Claude Code profillerini sistemde kendisi bulur (`$CODEX_HOME`, `~/.codex`, `$CLAUDE_CONFIG_DIR`, `~/.claude`, `~/.claude-*`) ve 5 saatlik / haftalık kota kullanımını kart olarak gösterir. Bulunamayan profil hiç görünmez.
- **GPU izleme** — panelden eklediğiniz makinelerde SSH ile `nvidia-smi` çalıştırır, boş/dolu GPU sayısını gösterir.
- **Sıfır yapılandırma** — tüm yerel ayarlar `watchdog_endpoints.json` dosyasında tutulur ve Git tarafından takip edilmez.

## Çalıştırma

Gereksinim: Python 3.9 veya daha yeni bir sürüm.

```bash
git clone https://github.com/KULLANICI/llm-endpoint-watchdog.git
cd llm-endpoint-watchdog
python3 llm-endpoint-watchdog.py
```

`KULLANICI` kısmını reponun GitHub kullanıcı adı ile değiştirin; GitHub'daki **Code** düğmesi hazır URL'yi verir.

Ardından tarayıcıda [http://localhost:9090](http://localhost:9090) adresini açın.

Panel ilk açılışta `127.0.0.1:8000` adresini izler. Başka bir OpenAI uyumlu LLM servisi eklemek için paneldeki alana `sunucu-ip:port` biçiminde adres girin:

```text
10.0.0.5:8000
```

## Codex / Claude kullanımı

Panel, aşağıdaki konumlarda oturum açmış hesapları otomatik olarak bulur:

| Araç | Aranan yer |
|------|------------|
| Codex CLI | `$CODEX_HOME/auth.json`, yoksa `~/.codex/auth.json` |
| Claude Code | `$CLAUDE_CONFIG_DIR`, `~/.claude/.credentials.json`, `~/.claude-*/.credentials.json` |

Ek bir ayar, API anahtarı veya yapılandırma gerekmez; ilgili araçta oturum açmış olmanız yeterlidir. Profiller çalışma sırasında da taranır, yeni bir hesap eklediğinizde panel kendiliğinden gösterir.

## GPU makineleri

Paneldeki **+ GPU Makinesi Ekle** düğmesiyle makine adı girin (ör. `gpu-node`). Makine adı SSH yapılandırmanızda tanımlı olmalıdır; komut yazmanız gerekmez, `nvidia-smi` sorgusu otomatik kurulur. İç içe SSH için `jump-host -> gpu-node` biçiminde yazabilirsiniz.

Eklediğiniz endpointler ve GPU makineleri yerel `watchdog_endpoints.json` dosyasına kaydedilir. Bu dosya Git tarafından takip edilmez.

Uygulamayı durdurmak için terminalde `Ctrl+C` tuşlarına basın.

## Ayarlar

Kontrol aralığı, panel portu ve zaman aşımı `llm-endpoint-watchdog.py` dosyasının başındaki sabitlerden değiştirilebilir:

```python
CHECK_INTERVAL = 10
DASHBOARD_PORT = 9090
TIMEOUT = 5
```
