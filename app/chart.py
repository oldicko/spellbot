"""A small score chart drawn as SVG. Values are integers, so the markup stays safe."""

from __future__ import annotations


def score_chart(scores: list[int]) -> str:
    if not scores:
        return ""
    values = [min(100, max(0, int(score))) for score in scores]
    width, height = 640, 220
    left, right, top, bottom = 36, 16, 18, 16
    plot_w = width - left - right
    plot_h = height - top - bottom

    def point(index: int, value: int) -> tuple[float, float]:
        if len(values) == 1:
            x = left + plot_w / 2
        else:
            x = left + plot_w * index / (len(values) - 1)
        y = top + plot_h * (1 - value / 100)
        return x, y

    coords = [point(index, value) for index, value in enumerate(values)]
    grid = []
    for label in (0, 50, 100):
        y = top + plot_h * (1 - label / 100)
        grid.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" />'
            f'<text x="0" y="{y + 4:.1f}">{label}</text>'
        )
    dots = []
    for index, (x, y) in enumerate(coords):
        last = index == len(coords) - 1
        radius = 6 if last else 4
        css = "dot last" if last else "dot"
        dots.append(f'<circle class="{css}" cx="{x:.1f}" cy="{y:.1f}" r="{radius}" />')
    line = ""
    if len(coords) > 1:
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        line = f'<polyline class="line" points="{poly}" />'
    title = f"Scores from recent sittings. The latest is {values[-1]} percent."
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{title}">'
        f"<title>{title}</title>"
        + "".join(grid)
        + line
        + "".join(dots)
        + "</svg>"
    )
