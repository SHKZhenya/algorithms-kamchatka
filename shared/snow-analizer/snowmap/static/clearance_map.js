(() => {
  const categories = [
    'Больницы и поликлиники', 'Экстренные службы', 'Администрация и госучреждения',
    'Школы, детсады и вузы', 'Жилые дома', 'Магазины и офисы',
    'Другие здания', 'Автобусные остановки',
  ];
  const sides = {N:'С', NE:'СВ', E:'В', SE:'ЮВ', S:'Ю', SW:'ЮЗ', W:'З', NW:'СЗ'};
  const nearbyRadius = 500;
  const panel = document.createElement('details');
  panel.className = 'clearance-panel';
  panel.hidden = true;
  panel.open = !matchMedia('(max-width: 730px)').matches;
  panel.innerHTML = `
    <summary>Расчистка</summary>
    <div class="clearance-options">
      <label><input type="checkbox" id="clearanceDoors" checked> Входы</label>
      <label><input type="checkbox" id="clearanceStops" checked> Остановки</label>
      <label><input type="checkbox" id="clearanceHealthcare" checked> Больницы и поликлиники</label>
      <label>Порог свежего снега, см <input type="number" id="clearanceLimit"
        min="0.1" step="any" value="20" required></label>
    </div>
    <small>↑ Вход · А Остановка · + Медучреждение · красный — достигнут порог · серый — нет оценки</small>
    <p id="clearanceStatus" role="status"></p>
    <button type="button" id="clearanceReset" hidden>Показать всю видимую область</button>
    <div id="clearanceList"></div>`;
  $('stats').after(panel);
  const css = document.createElement('style');
  css.textContent = `
    .clearance-panel{position:fixed;z-index:2;right:16px;top:96px;width:min(350px,calc(100vw - 32px));
      max-height:calc(100dvh - 164px);overflow:auto;padding:12px 14px;background:white;
      border:1px solid var(--line);border-radius:12px;box-shadow:0 5px 24px #10273d22}
    .clearance-panel:not([open]){width:auto}
    .clearance-panel > summary{font-weight:700;cursor:pointer}
    .clearance-panel[open] > summary{margin-bottom:8px}
    @media(max-width:730px){.clearance-panel{right:10px;top:auto;bottom:118px;max-height:42dvh}}
    .clearance-options{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;margin-bottom:8px}
    .clearance-options label{display:flex;align-items:center;gap:6px}
    .clearance-options input[type=number]{width:5em}
    #clearanceList{max-height:280px;overflow:auto}
    #clearanceList details{padding:5px 0}
    #clearanceList button,#clearanceReset{background:transparent;color:#15476a;text-align:left;
      text-decoration:underline;padding:4px;border:0;cursor:pointer}
    .clearance-marker span{display:grid;place-items:center;box-sizing:border-box;width:21px;height:21px;
      border-radius:50%;background:#17658a;color:white;border:2px solid white;
      box-shadow:0 1px 4px #24384c80;font:bold 15px/1 system-ui}
    .clearance-marker .blocked{background:#9b1828}
    .clearance-marker .unknown{background:#53606b}
    .clearance-marker .medical{outline:2px solid #6b38a2}
  `;
  document.head.append(css);

  let inventory, loading, error, layer, selected, frame;
  const markers = new Map();
  const limit = $('clearanceLimit');
  const reset = $('clearanceReset');
  const status = $('clearanceStatus');
  const list = $('clearanceList');

  function gridValue(tiles, lat, lon) {
    for (const {data} of tiles) {
      const [west, south, east, north] = data.bbox_wgs84;
      if (lon < west || lon >= east || lat <= south || lat > north) continue;
      const col = Math.floor((lon - west) / (east - west) * data.cols);
      const row = Math.floor((north - lat) / (north - south) * data.rows);
      return data.snow_depth_cm[row]?.[col];
    }
  }

  function snowAt(tiles, point) {
    if (point.samples) {
      let maximum = null;
      for (const [lat, lon] of point.samples) {
        const depth = gridValue(tiles, lat, lon);
        if (Number.isFinite(depth) && (maximum === null || depth > maximum)) maximum = depth;
      }
      return maximum;
    }
    if (!point.building) {
      const depth = gridValue(tiles, point.lat, point.lon);
      return Number.isFinite(depth) ? depth : null;
    }
    if (point.bearing === null) return null;
    // Advance along the exterior normal only until we leave the raster's building mask.
    const rad = Math.PI / 180;
    const angle = point.bearing * rad;
    const north = Math.cos(angle) / 111195;
    const east = Math.sin(angle) / (111195 * Math.cos(point.lat * rad));
    for (const metres of [1, 2, 3, 4, 5]) {
      const depth = gridValue(tiles, point.lat + metres * north, point.lon + metres * east);
      if (Number.isFinite(depth)) return depth;
      if (depth === undefined) break;
    }
    return null;
  }

  function title(point) {
    if (!point.name && point.address) return point.address;
    const name = point.name || 'Здание ' + point.building;
    return `${name} · ${point.address || 'адрес не указан'}`;
  }

  function snowText(point, depth, blocked) {
    const measurement = depth === null ? 'нет оценки снега' :
      `${fmt.format(depth)} см свежего снега`;
    const area = point.samples ? 'Максимум примерно в 5 м от стен: ' : '';
    return area + measurement + (blocked ? ' · нужна расчистка' : '');
  }

  function updateMarker(point, depth, blocked) {
    const state = depth === null ? 'unknown' : blocked ? 'blocked' : '';
    const markerTitle = title(point);
    let item = markers.get(point.id);
    if (!item) {
      const popup = document.createElement('div');
      const marker = L.marker([point.lat, point.lon], {
        title: markerTitle, alt: markerTitle,
        icon: L.divIcon({className:'clearance-marker', iconSize:[21, 21]}),
      }).bindPopup(popup).addTo(layer);
      marker.on('click', () => { selected = point; schedule(); });
      item = {marker, popup};
      markers.set(point.id, item);
    }
    if (item.state !== state) {
      const icon = document.createElement('span');
      icon.textContent = point.samples ? '+' :
        point.building ? (point.bearing === null ? '•' : '↑') : 'А';
      icon.className = state;
      if (point.samples) icon.classList.add('medical');
      if (point.bearing !== null && point.building) icon.style.transform = `rotate(${point.bearing}deg)`;
      item.marker.setIcon(L.divIcon({className:'clearance-marker', iconSize:[21, 21], html:icon}));
      item.state = state;
    }
    const label = document.createElement('strong');
    label.textContent = markerTitle;
    const details = document.createElement('div');
    const direction = point.side ? ' · ' + sides[point.side] : '';
    const roles = {main:'главный вход', staircase:'подъезд', service:'служебный вход',
      home:'вход в дом', shop:'вход в магазин', exit:'выход', emergency:'аварийный выход'};
    const role = point.exit === 'emergency' ? roles.emergency :
      roles[point.role] || (point.building ? (point.role ? 'вход' : 'выход') : '');
    details.textContent = point.category + (role ? ' · ' + role : '') + direction;
    const snowLabel = document.createElement('div');
    snowLabel.textContent = snowText(point, depth, blocked);
    if (item.depth !== depth || item.blocked !== blocked) {
      item.popup.replaceChildren(label, details, snowLabel);
      item.depth = depth;
      item.blocked = blocked;
    }
  }

  function renderList(candidates) {
    const groups = new Map();
    const expanded = new Map([...list.querySelectorAll('details')].map(node =>
      [node.dataset.category, node.open]));
    let blockedCount = 0;
    for (const candidate of candidates.values()) {
      const key = candidate.point.category;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(candidate);
      if (candidate.blocked) blockedCount++;
    }
    list.replaceChildren();
    for (const category of categories) {
      const entries = groups.get(category);
      if (!entries) continue;
      entries.sort((a, b) => Number(b.blocked) - Number(a.blocked) ||
        Number(a.depth === null) - Number(b.depth === null) || a.distance - b.distance);
      const section = document.createElement('details');
      section.dataset.category = category;
      section.open = expanded.get(category) ?? true;
      const summary = document.createElement('summary');
      summary.textContent = `${category} · ${entries.length}`;
      const ul = document.createElement('ul');
      for (const {point, depth, distance, blocked} of entries) {
        const li = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = `${title(point)} · ${snowText(point, depth, blocked)} · ${Math.round(distance)} м`;
        button.onclick = () => {
          selected = point;
          markers.get(point.id)?.marker.openPopup();
          schedule();
        };
        li.append(button);
        ul.append(li);
      }
      section.append(summary, ul);
      list.append(section);
    }
    reset.hidden = !selected;
    const area = selected ? `В пределах ${nearbyRadius} м от ${title(selected)} на видимой карте` : 'В видимой области';
    status.textContent = `${area}. Объектов — ${candidates.size}, к расчистке — ${blockedCount}.`;
  }

  function render() {
    frame = null;
    panel.hidden = $('source').value !== 'map';
    if (panel.hidden || !map) return;
    if (!layer) {
      layer = L.layerGroup().addTo(map);
      map.on('moveend', schedule);
    }
    if (!inventory) {
      status.textContent = error || 'Загружаю объекты…';
      if (!loading) {
        loading = fetch('/clearance/points').then(response => {
          if (!response.ok) throw new Error('Не удалось загрузить объекты.');
          return response.json();
        }).then(data => { inventory = data; schedule(); }).catch(() => {
          error = 'Не удалось загрузить объекты.';
          schedule();
        });
      }
      return;
    }
    const threshold = limit.validity.valid ? limit.valueAsNumber : null;
    const tiles = [...snowTiles.values()];
    const bounds = map.getBounds();
    const center = selected ? [selected.lat, selected.lon] : map.getCenter();
    const displayed = new Set();
    let unassessed = 0;
    const candidates = new Map();
    const points = [
      ...($('clearanceDoors').checked ? inventory.doors : []),
      ...($('clearanceStops').checked ? inventory.stops : []),
      ...($('clearanceHealthcare').checked ? inventory.healthcare ?? [] : []),
    ];
    for (const point of points) {
      const where = [point.lat, point.lon];
      if (!bounds.contains(where)) continue;
      const depth = snowAt(tiles, point);
      if (depth === null) unassessed++;
      const blocked = depth !== null && threshold !== null && depth >= threshold;
      updateMarker(point, depth, blocked);
      displayed.add(point.id);
      const distance = map.distance(center, where);
      if (selected && distance > nearbyRadius) continue;
      const key = point.building ? 'entrances/' + point.building : point.id;
      const previous = candidates.get(key);
      if (!previous || (depth ?? -Infinity) > (previous.depth ?? -Infinity)) {
        candidates.set(key, {point, depth, distance, blocked});
      }
    }
    for (const [id, item] of markers) {
      if (displayed.has(id)) continue;
      layer.removeLayer(item.marker);
      markers.delete(id);
    }
    renderList(candidates);
    if (!tiles.length) status.textContent += ' Нет рассчитанных тайлов снега.';
    else if (!threshold) status.textContent += ' Укажите положительный порог.';
    else if (unassessed) status.textContent += ` Точек без оценки — ${unassessed}.`;
  }

  function schedule() {
    if (frame == null) frame = requestAnimationFrame(render);
  }

  reset.onclick = () => { selected = null; schedule(); };
  window.addEventListener('clearancechange', schedule);
  $('source').addEventListener('change', schedule);
  $('clearanceDoors').addEventListener('change', schedule);
  $('clearanceStops').addEventListener('change', schedule);
  $('clearanceHealthcare').addEventListener('change', schedule);
  limit.addEventListener('input', schedule);
  schedule();
})();
