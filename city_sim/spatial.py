"""Raster/object intersections. No interpolation or invented subcell precision.

Grid origin is NW, cells are half-open east/south, values are cell constants.
Lengths/areas are measured in the project's metric CRS; statistics are weighted.
"""
import math
import numpy as np
from shapely.geometry import Point, box, mapping
from shapely.ops import transform
from weather_coverage.geography import TO_METRIC


class RasterAssessment:
    def __init__(self, grid, bounds, metric_coordinates=False):
        self.metric_coordinates = metric_coordinates
        self.grid = np.asarray(grid, dtype=float)
        if self.grid.ndim != 2 or not self.grid.size:
            raise ValueError('Expected a nonempty 2-D raster')
        self.w, self.s, self.e, self.n = bounds
        self.rows, self.cols = self.grid.shape
        if not self.w < self.e or not self.s < self.n:
            raise ValueError('Invalid raster bounds')
        self.dx = (self.e-self.w)/self.cols
        self.dy = (self.n-self.s)/self.rows

    def point(self, point):
        if point.is_empty:
            return None
        if not (self.w <= point.x < self.e and self.s < point.y <= self.n):
            return None
        i = min(self.cols-1, int((point.x-self.w)/self.dx))
        j = min(self.rows-1, int((self.n-point.y)/self.dy))
        value = float(self.grid[j, i])
        return value if math.isfinite(value) else None

    def _parts(self, geom):
        """Split each line leg at grid crossings; preserve revisited cells/length."""
        if geom.is_empty:
            return
        if geom.geom_type == 'Point':
            yield geom, 1., self.point(geom)
        elif geom.geom_type in {'LineString', 'LinearRing'}:
            coords = list(geom.coords)
            from shapely.geometry import LineString
            for a, b in zip(coords, coords[1:]):
                cuts = [0., 1.]
                for start, end, origin, step, count in ((a[0], b[0], self.w, self.dx, self.cols),
                                                        (a[1], b[1], self.s, self.dy, self.rows)):
                    if end == start:
                        continue
                    lo = max(0, math.ceil((min(start, end)-origin)/step))
                    hi = min(count, math.floor((max(start, end)-origin)/step))
                    cuts.extend(t for k in range(lo, hi+1) if 0 < (t := (origin+k*step-start)/(end-start)) < 1)
                cuts = sorted(set(cuts))
                for l, r in zip(cuts, cuts[1:]):
                    part = LineString([(a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])) for t in (l, r)])
                    yield part, self._metric(part).length, self.point(part.interpolate(.5, normalized=True))
        elif geom.geom_type == 'Polygon':
            x0, y0, x1, y1 = geom.bounds
            ilo, ihi = max(0, math.floor((x0-self.w)/self.dx)), min(self.cols-1, math.floor((x1-self.w)/self.dx))
            jlo, jhi = max(0, math.floor((self.n-y1)/self.dy)), min(self.rows-1, math.floor((self.n-y0)/self.dy))
            for j in range(jlo, jhi+1):
                for i in range(ilo, ihi+1):
                    part = geom.intersection(box(self.w+i*self.dx, self.n-(j+1)*self.dy,
                                                 self.w+(i+1)*self.dx, self.n-j*self.dy))
                    if part.is_empty:
                        continue
                    weight = self._metric(part).area
                    if weight > 0:
                        value = float(self.grid[j, i])
                        yield part, weight, value if math.isfinite(value) else None
        elif hasattr(geom, 'geoms'):
            for part in geom.geoms:
                yield from self._parts(part)
        else:
            raise ValueError('Unsupported assessment geometry')

    def assess(self, geom, threshold_cm=20, fragments=True):
        if not math.isfinite(threshold_cm) or threshold_cm < 0:
            raise ValueError('Threshold must be finite and nonnegative')
        dimension = 'area_m2' if 'Polygon' in geom.geom_type else 'point' if geom.geom_type == 'Point' else 'length_m'
        metric = self._metric(geom)
        total = metric.area if dimension == 'area_m2' else 1. if dimension == 'point' else metric.length
        values, problems = [], []
        above = 0.
        for part, weight, value in self._parts(geom):
            if value is None or weight <= 0:
                continue
            values.append((value, weight))
            if value >= threshold_cm:
                above += weight
                if fragments:
                    problems.append(mapping(part))
        valid = sum(w for _, w in values)
        ordered = sorted(values)
        cumulative, p90 = 0., None
        for v, weight in ordered:
            cumulative += weight
            if cumulative >= valid*.9:
                p90 = v
                break
        center = geom.representative_point() if geom.is_empty else geom if dimension == 'point' else geom.interpolate(.5, normalized=True) if dimension == 'length_m' else geom.representative_point()
        latitude = center.y if not center.is_empty else (self.n+self.s)/2
        return {'mean_cm': sum(v*w for v, w in values)/valid if valid else None,
                'max_cm': max((v for v, _ in values), default=None), 'p90_cm': p90,
                'midpoint_baseline_cm': self.point(center), 'measure': dimension, 'total': total,
                'covered': valid, 'missing': max(0., total-valid), 'coverage_fraction': min(1., valid/total) if total else 0.,
                'above_threshold': above, 'above_fraction_total': above/total if total else None,
                'above_fraction_covered': above/valid if valid else None,
                'problem_fragments': problems, 'threshold_cm': threshold_cm,
                'threshold_status': 'experimental_not_normative',
                'cell_width_m': self.dx if self.metric_coordinates else self.dx*111195*math.cos(math.radians(latitude)),
                'cell_height_m': self.dy if self.metric_coordinates else self.dy*111195,
                'method': 'cell_intersections_metric_weighted', 'field_validated': False}

    def _metric(self, geom):
        return geom if self.metric_coordinates else transform(TO_METRIC, geom)
