"""Build a standalone, static set of annotated research cards."""
from html import escape
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "sources"
CASES = json.loads((HERE / "cases.json").read_text())
FRAMES = {f["id"]: f for f in json.loads((SOURCE / "frames.json").read_text()) if f.get("file")}
VIDEOS = json.loads((SOURCE / "videos.json").read_text())
MODELS = json.loads((HERE / "model_samples.json").read_text())
MAP_REFERENCE = json.loads((SOURCE / "map_reference.json").read_text())
SYMBOLS = "ABC"


def num(value, digits=0):
    return f"{value:.{digits}f}".replace(".", ",")


def span(value, digits=0):
    low, high = num(value["min"], digits), num(value["max"], digits)
    return low if low == high else f"{low}–{high}"


def credit(identifier):
    for prefix, author in (("dec-aif", "АиФ Камчатка · фото из соцсетей"),
                           ("house", "Александр Пирагис / РИА Новости · публикация РБК"),
                           ("ria", "Александр Пирагис / РИА Новости"),
                           ("report", "ИА KamchatkaMedia"),
                           ("kam24", "ИА Кам 24"),
                           ("kedr", "Марина Сычева / Кедр"),
                           ("video-", "Телеканал 360 · фрагмент репортажа")):
        if identifier.startswith(prefix):
            return author
    raise ValueError(identifier)


def model_text(identifier):
    entry = MODELS["city"]["photos"].get(identifier)
    if not entry:
        return ("Не определено для точки снимка. Геопривязки до ячейки или сопоставимой площадки нет.",
                "Нет рассчитанного кадра для этой локации и срока.",
                "Визуальное наблюдение. Совпадение с моделями не проверено.")
    lon, lat = entry["landmark_lon_lat"]
    place = f"<small>Ориентир {lat:.5f}° с.ш., {lon:.5f}° в.д. · радиус {entry['radius_m']} м</small>"
    rows = entry["frames"]
    low = min(row["danil_fresh_cm"]["min"] for row in rows)
    high = max(row["danil_fresh_cm"]["max"] for row in rows)
    if identifier == "house-42":
        quarter = MODELS["quarter_4_2"]
        q = quarter["hourly_fresh_cm"]
        city = "За цикл с 12.01. Медиана по окрестности "
        city += ", ".join(f"{num(r['danil_fresh_cm']['median'])} см к {r['local_time'][11:16]}" for r in rows)
        city += f". Разброс по двум срокам и всем ячейкам {num(low)}–{num(high)} см."
        cell = quarter["northwest_wall"]["cells"][0]
        face = cell["fresh_cm"]
        city += f" В одной ячейке у северо-западного торца за час {num(face['danil'], 2)} см."
        city += f" В часовом тесте квартала {span(q['danil'], 2)} см."
        openfoam = f"{span(q['cfd'], 2)} см свежего снега на срок 15.01, 17:00 UTC+12."
        openfoam += f" Диапазон по 15 ячейкам у дома. У северо-западного торца расчёт {num(face['cfd'], 2)} см, равномерный фон {num(face['uniform'], 2)} см."
        decision = ("Фото подтверждает локальное накопление. Медиана Данила у дома выше примерно 78% значений "
                    "в радиусе 350 м. Это согласуется с общей картиной высокого снега у адреса. "
                    "У северо-западного торца Данил прогнозирует усиление за час, OpenFOAM — ослабление. "
                    "Снимок показывает накопленный снег. Час снимка и сопоставимый вид другой стороны неизвестны. "
                    "Величину ошибки и относительный выигрыш моделей определить нельзя.")
    else:
        city = f"Исторический контекст 13–17.01, свежий снег с 12.01. Диапазон {num(low)}–{num(high)} см по {len(rows)} срокам и всем ячейкам радиуса."
        first, last = rows[0]["b0_cm"]["min"], rows[-1]["b0_cm"]["max"]
        city += f" Равномерный фон за те же сроки {num(first)}–{num(last)} см."
        openfoam = "Вне рассчитанного квартала."
        decision = ("Уборочный вал исключён из проверки осаждения. Фотодата известна в пределах репортажа. "
                    "18–19 января отсутствуют в архиве прогона." if identifier == "report-26" else
                    "Фото показывает ровное поле. В выборке Данил совпадает с равномерным фоном на всех десяти сроках. "
                    "Это слабое качественное согласие с видимой однородностью. Подтвердить дату кадра внутри периода "
                    "прогона и низкую абсолютную высоту нельзя. 18–19 января отсутствуют в архиве прогона.")
    return city + place, openfoam, decision


def make_card(case, number):
    identifier, title, place, date, kind, estimate, limits, *marks = case
    source = FRAMES[identifier]
    danil, openfoam, decision = model_text(identifier)
    dots = "".join(f'<span aria-hidden="true" class="dot {letter}" style="--x:{x}%;--y:{y}%">{letter}</span>'
                   for letter, (x, y, _) in zip(SYMBOLS, marks))
    guide = "".join(f'<li><b class="{letter}">{letter}</b> {escape(label)}</li>'
                    for letter, (_, _, label) in zip(SYMBOLS, marks))
    if identifier == "kedr-25":
        dots += '<span aria-hidden="true" class="bracket" style="--x:44%;--y:42%;--h:32%"></span>'
    options = f'<a href="{escape(source["publication"], quote=True)}">Публикация</a> · '
    options += f'<a href="{escape(source["file"], quote=True)}">Кадр без разметки</a> · '
    options += f'<a href="{escape(source["url"], quote=True)}">Файл источника</a>'
    if identifier == "house-42":
        options += f' · <a href="{escape(MAP_REFERENCE["url"], quote=True)}">Панорама Яндекса</a>'
        options += f' · <a href="{escape(MAP_REFERENCE["public_preview_url"], quote=True)}">Фото фасада без снега</a>'
    if identifier in MODELS["city"]["photos"]:
        osm = MODELS["city"]["photos"][identifier]["osm_ways"][0]
        options += f' · <a href="https://www.openstreetmap.org/way/{osm}">Ориентир на карте</a>'
    width, height = source["size"]
    return f'''<article id="{identifier}">
 <h2><a class="number" href="#{identifier}" aria-label="Ссылка на карточку {number}">{number:02}</a>{escape(title)}</h2>
 <p class="place">{escape(place)}<br><strong>{escape(date)}</strong></p>
 <div class="content">
 <figure><div class="media"><img width="{width}" height="{height}" loading="lazy" src="{source["file"]}" alt="{escape(title)}">{dots}</div>
 <figcaption><ul class="marks">{guide}</ul><p>{credit(identifier)} · {options}</p></figcaption></figure>
 <dl><dt>Тип снега</dt><dd>{escape(kind)}</dd>
 <dt>Наблюдение</dt><dd>{escape(estimate)}</dd>
 <dt>Масштаб и погрешность</dt><dd>{escape(limits)}</dd>
 <dt>Данил</dt><dd>{danil}</dd><dt>OpenFOAM</dt><dd>{openfoam}</dd>
 <dt>Сопоставление</dt><dd>{decision}</dd></dl>
 </div>
</article>'''


def media_registry():
    consulted = {row[0] for row in CASES}
    photos = []
    for key, frame in FRAMES.items():
        mark = f'<a href="#{key}">Есть карточка</a>' if key in consulted else 'Резерв'
        name = frame.get("caption") or key
        photos.append(f'<li>{escape(name)} · {credit(key)} · {mark} · '
                      f'<a href="{escape(frame["file"], quote=True)}">Кадр</a> · '
                      f'<a href="{escape(frame["publication"], quote=True)}">Публикация</a></li>')
    videos = [f'<li>{escape(v["date"])} · {escape(v["source"])}. {escape(v["status"])} '
              f'<a href="{escape(v["url"], quote=True)}">Видео</a></li>' for v in VIDEOS]
    return '<h3>Фотографии и извлечённые кадры</h3><ol>' + ''.join(photos) + '</ol><h3>Видео</h3><ul>' + ''.join(videos) + '</ul>'


STYLES = '''
*{box-sizing:border-box}
:root{color-scheme:light;--ink:#182534;--muted:#536476;--line:#d5dce4;--navy:#164564}
body{margin:0;background:#f3f5f7;color:var(--ink);font:16px/1.52 system-ui,sans-serif}
main{max-width:1120px;padding:30px 30px 70px;margin:auto}
header{padding:18px 0 34px}
h1{font-size:clamp(32px,4.6vw,58px);line-height:1.05;letter-spacing:-.033em;max-width:900px;margin:.2em 0}
h2{font-size:23px;line-height:1.2;margin:0 0 10px}
header p{max-width:850px}
a{color:#135a87;text-underline-offset:3px}
a:focus-visible{outline:3px solid #1c628e;outline-offset:3px}
article,.panel{background:white;border:1px solid var(--line);border-radius:9px;padding:22px;margin-bottom:19px}
.number{display:inline-block;margin-right:10px;color:var(--muted);font-size:.78em;font-variant-numeric:tabular-nums}
.place{color:var(--muted);margin:.45em 0 1em}
.content{display:grid;grid-template-columns:minmax(0,1.21fr) minmax(0,1fr);gap:24px}
figure{margin:0}
.media{position:relative;background:#edf0f4;border-radius:6px;overflow:hidden}
img{display:block;width:100%;height:auto}
.dot,.marks b{color:#fff;background:#203147;border-radius:100%;display:inline-grid;place-content:center;width:24px;height:24px;font-size:13px;line-height:1;font-weight:800}
.dot{position:absolute;top:var(--y);left:var(--x);transform:translate(-50%,-50%);border:2px solid white;box-shadow:0 0 0 1px #152335;z-index:1}
.dot.A,.marks b.A{background:#095d60}.dot.B,.marks b.B{background:#9c3c11}
.bracket{position:absolute;left:var(--x);top:var(--y);height:var(--h);width:12px;border:2px solid #9c3c11;border-right:none;filter:drop-shadow(0 0 1px white)}
.marks{list-style:none;padding:0;margin:10px 0}
.marks li{margin-bottom:5px}
.marks b{width:19px;height:19px;margin-right:3px}
figcaption,small{font-size:13px;color:var(--muted)}
figcaption p{margin:.65em 0 0}
dl{margin:0;font-size:14px}
dt{font-weight:760;margin:0 0 3px;color:#254357}
dd{margin:0 0 .85em} dd:last-child{margin-bottom:0}
small{display:block;margin-top:5px}
nav ol{columns:2;padding-left:23px}
.panel li{padding-bottom:.3em}
details summary{cursor:pointer;font-weight:700}
table{border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:.35em .7em;border-bottom:1px solid var(--line);text-align:right}
th:first-child{text-align:left}
@media(max-width:740px){main{padding:16px}.content{grid-template-columns:1fr;gap:16px}nav ol{columns:1}article,.panel{padding:16px}}
@media print{body{background:white}main{padding:0}article{break-inside:avoid}article, .panel{border:1px solid #aaa}.dot,.marks b{print-color-adjust:exact}}
'''


def html_page():
    toc = "".join(f'<li><a href="#{r[0]}">{escape(r[1])}</a></li>' for r in CASES)
    cards = "".join(make_card(case, n) for n, case in enumerate(CASES, 1))
    q = MODELS["quarter_4_2"]["sectors"]
    table = "".join(f"<tr><th>{ru}</th><td>{q[tag]['cfd']['n']}</td>"
                    f"<td>{num(q[tag]['danil']['median'],2)}</td>"
                    f"<td>{num(q[tag]['cfd']['median'],2)}</td></tr>"
                    for tag, ru in zip(("N","S","E","W"), ("Север","Юг","Восток","Запад")))
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Снег в Петропавловске-Камчатском · разбор кадров</title><style>{STYLES}</style></head>
<body><main><header>
<p>Ретроспективная валидация · декабрь 2025 и январь 2026</p>
<h1>Снег в Петропавловске-Камчатском</h1>
<p>{len(CASES)} карточек с историческими кадрами. У трёх есть подтверждённый ориентир и значения Данила за часть возможного периода. У дома 4/2 есть также архивный расчёт OpenFOAM и заметное расхождение двух моделей по сторонам дома. Панорама помогла опознать северо-западный торец. Час зимней съёмки и история снега у торца пока неизвестны.</p>
<p><strong>A</strong> — масштаб или ориентир. <strong>B</strong> — снег в зоне оценки. <strong>C</strong> — нижний уровень, контрольный участок или дополнительный ориентир. Оценки относятся к указанным участкам кадра.</p>
<p><a href="REPORT.md">Выводы и методика</a> · <a href="model_samples.json">Таблицы по срокам и ячейкам</a> · <a href="#registry">Реестр фото и видео</a></p>
</header>
<nav class="panel" aria-label="Перечень карточек"><h2>Кадры</h2><ol>{toc}</ol></nav>
{cards}
<section class="panel" id="quarter"><h2>Северо-западный торец дома 4/2</h2>
<p>Совпали зелёная табличка, красная вертикальная секция с парными окнами и светлая обшивка. Панорама снята в 7,7 м от соответствующего торца. Нормаль к торцу по контуру OSM направлена на 318°. Просмотр Google Maps не дал подтверждённого кадра. <a href="{escape(MAP_REFERENCE['url'],quote=True)}">Панорама Яндекс Карт</a> · <a href="{escape(MAP_REFERENCE['public_preview_url'],quote=True)}">Превью фасада</a></p>
<p>Ближайшая сопоставимая ячейка у торца имеет центр в 11,5 м от стены. 15 января на срок 17:00 Данил дал 3,50 см, равномерный снегопад — 2,94 см, OpenFOAM с моделью осаждения — 1,02 см.</p>
<figure style="max-width:680px"><img src="facade-42.png" alt="Схема положения панорамы Яндекса, северо-западного торца дома 4/2 и ближайшей ячейки"></figure>
<h3>Поля по всему кварталу</h3>
<p>15 января 2026 года, 17:00 UTC+12. Медианы свежего снега, см. Сектора заданы относительно центра контура дома. Единица выборки — 24-метровая ячейка. В равномерном сценарии во всех секторах 2,94 см.</p>
<table><thead><tr><th>Сектор</th><th>Ячеек</th><th>Данил</th><th>OpenFOAM</th></tr></thead><tbody>{table}</tbody></table>
<figure><a href="openfoam-15jan.png"><img src="openfoam-15jan.png" alt="Карты расчётов снега и ветра на квартале за 15 января в 17:00" loading="lazy"></a></figure></section>
<section class="panel" id="registry"><h2>Реестр материалов</h2>{media_registry()}</section>
</main></body></html>'''


if __name__ == "__main__":
    (HERE / "index.html").write_text(html_page())
