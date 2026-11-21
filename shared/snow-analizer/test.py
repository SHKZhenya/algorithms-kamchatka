import overpass

# Указываем эндпоинт VK Maps и уникальный User-Agent
api = overpass.API(
    endpoint="https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    user_agent="KamchaStudyScript/1.0",
    timeout=60,
)

# Библиотека сама добавляет [out:json] в начало и out geom; в конец,
# поэтому пишем только тело запроса
query = """
(
  node["amenity"="cafe"](51.500,-0.140,51.515,-0.115);
  way["amenity"="cafe"](51.500,-0.140,51.515,-0.115);
);
"""

# По умолчанию возвращает словарь в формате GeoJSON
geojson_data = api.get(query)

for feature in geojson_data["features"]:
    props = feature["properties"]
    geom = feature["geometry"]
    name = props.get("name") or props.get("tags", {}).get("name") or "Без названия"
    print(f"{name} | Тип: {geom['type']} | Координаты: {geom['coordinates']}")
