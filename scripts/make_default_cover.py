#!/usr/bin/env python3
"""Draw the default realm cover:
     python3 scripts/make_default_cover.py > src/realm_frontend/static/images/background.svg
   then render background.png, the still used where a moving picture cannot be, from it.

The idea is the "world as a computer" of the gos.earth presentation, without its game-like scenery: the world below,
and above it the three layers that run on it. From the bottom: institutions (the operating system, in the teal the
presentation gives the GOS), services (a network) and people (points of light). Nothing in it is a word.

The picture has no size of its own. The world fills the frame like a cover photo with its horizon a little below
mid-height; the stack is always whole and stands on the horizon, so the same file works behind a wide home page, a tall phone and the
half-width panel of the join page. Motion is slow and stops for readers who ask for reduced motion.
"""
import math, random

TEAL, TEAL_DARK = '#6BA3A3', '#2F5F5F'
R, K = 250, 0.27                       # a layer: a disc of radius R seen at a slant (flattened by K)
Y_PEOPLE, Y_SERVICES, Y_OS, BASE = 92, 192, 292, 420
CX, BOX = 280, 560                     # the stack's box is BOX wide and BASE tall
rnd = random.Random(7)

def dot(x, y, cls, extra=''):
    # a zero-length round-capped stroke: a round dot whatever the layer's slant and turn
    return f'<path d="M{x:.1f} {y:.1f}h.01" class="{cls}"{extra}/>'

def layer(cy, body, tint=None, turn=None):
    fill = tint or '#fff'
    inner = f'<g class="{turn}">{body}</g>' if turn else body
    return f'''
    <ellipse cx="{CX}" cy="{cy + 58}" rx="{R * .92:.0f}" ry="{R * K * .8:.0f}" fill="url(#shade)"/>
    <g class="float" style="animation-delay:{-cy / 30:.1f}s">
      <ellipse cx="{CX}" cy="{cy + 5}" rx="{R}" ry="{R * K:.1f}" fill="#eceef1" stroke="#d3d7dd"/>
      <g transform="translate({CX} {cy}) scale(1 {K})">
        <circle r="{R}" fill="{fill}" fill-opacity=".94" class="disc"/>
        {inner}
      </g>
    </g>'''

# --- institutions: rings in orderly motion, the operating system ---------------------------------------------------
os_body = ''.join(f'<circle r="{r}" class="ring"/>' for r in (76, 142, 208))
os_body += '<g class="turn-slow">' + ''.join(
    f'<path d="M{208 * math.cos(a):.1f} {208 * math.sin(a):.1f}L{224 * math.cos(a):.1f} {224 * math.sin(a):.1f}" class="tick"/>'
    for a in (i * math.tau / 48 for i in range(48))) + '</g>'
for r, secs, n in ((76, 46, 1), (142, 74, 2), (208, 110, 3)):
    marks = ''.join(dot(r * math.cos(i * math.tau / n + r), r * math.sin(i * math.tau / n + r), 'os-dot') for i in range(n))
    os_body += f'<g style="animation:turn {secs}s linear infinite">{marks}</g>'

# --- services: a network with traffic on it ------------------------------------------------------------------------
nodes = [(-164, -44), (-100, 78), (-68, -122), (2, -8), (48, 122), (78, -100), (132, 24), (178, -68), (-188, 68), (12, 188), (-10, -196)]
links = [(0, 1), (0, 2), (1, 3), (2, 3), (2, 5), (3, 5), (3, 6), (3, 4), (4, 6), (5, 7), (6, 7), (1, 8), (0, 8), (4, 9), (1, 9), (2, 10), (5, 10)]
net = ''.join(f'<path d="M{nodes[a][0]} {nodes[a][1]}L{nodes[b][0]} {nodes[b][1]}" class="link"/>' for a, b in links)
net += ''.join(f'<path d="M{nodes[a][0]} {nodes[a][1]}L{nodes[b][0]} {nodes[b][1]}" class="flow" style="animation-delay:{-i * 1.7:.1f}s"/>'
               for i, (a, b) in enumerate(links[::2]))
net += ''.join(dot(x, y, 'node') for x, y in nodes)

# --- people: points of light ---------------------------------------------------------------------------------------
people, tries = [], 0
while len(people) < 60 and tries < 20000:
    tries += 1
    a, r = rnd.uniform(0, math.tau), 222 * math.sqrt(rnd.random())
    p = (r * math.cos(a), r * math.sin(a))
    if all(math.dist(p, q) > 30 for q in people):
        people.append(p)
crowd = ''.join(dot(x, y, 'person' + (' lit' if i % 3 == 0 else ''), f' style="animation-delay:{-rnd.uniform(0, 7):.1f}s"' if i % 3 == 0 else '')
                for i, (x, y) in enumerate(people))

# --- what passes between the layers --------------------------------------------------------------------------------
rising = ''
for i, x in enumerate((CX - 130, CX - 62, CX, CX + 66, CX + 128)):
    for j, (y0, y1) in enumerate(((BASE, Y_OS), (Y_OS, Y_SERVICES), (Y_SERVICES, Y_PEOPLE))):
        rising += (f'<circle cx="{x}" cy="{y0}" r="2.8" class="rise" style="--dy:{y1 - y0}px;'
                   f'animation-delay:{-(i * 1.3 + j * 2.1):.1f}s"/>')
frame = ''.join(f'<path d="M{x} {Y_PEOPLE}V{BASE}" class="guide"/>' for x in (CX - R, CX, CX + R))

# --- the world -----------------------------------------------------------------------------------------------------
PR, PCX, PCY = 2600, 800, 500 + 2600
arcs = ''.join(f'<circle cx="{PCX}" cy="{PCY}" r="{PR - d}" fill="none" stroke="#bfc5cd" class="ripple" '
               f'style="--o:{o};animation-delay:{i * 1.4 - 9:.1f}s"/>'
               for i, (d, o) in enumerate(((64, .6), (140, .44), (228, .3), (330, .18))))
print(f'''<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%">
<!-- The default cover of a realm. Generated by make_cover.py: change the picture there. -->
<style>
  .ring, .tick, .link, .flow, .guide, .node, .person, .os-dot {{ vector-effect: non-scaling-stroke; fill: none; }}
  .disc {{ vector-effect: non-scaling-stroke; stroke: #c3c8d0; stroke-width: 1; }}
  .ring {{ stroke: {TEAL}; stroke-opacity: .55; stroke-width: 1; }}
  .tick {{ stroke: {TEAL}; stroke-opacity: .7; stroke-width: 1; }}
  .os-dot {{ stroke: {TEAL_DARK}; stroke-width: 7; stroke-linecap: round; }}
  .link {{ stroke: #c3c8cf; stroke-width: 1; }}
  .node {{ stroke: #6b7280; stroke-width: 6; stroke-linecap: round; }}
  .flow {{ stroke: #262626; stroke-width: 3; stroke-linecap: round; stroke-dasharray: 5 400; animation: flow 6s linear infinite; }}
  .person {{ stroke: #9aa0aa; stroke-width: 5; stroke-linecap: round; }}
  .person.lit {{ stroke: #404040; animation: glow 7s ease-in-out infinite; }}
  .guide {{ stroke: #cfd4da; stroke-width: 1; stroke-dasharray: 2 6; }}
  .rise {{ fill: {TEAL}; animation: rise 6.5s ease-in-out infinite; }}
  .ripple {{ stroke-opacity: var(--o); animation: ripple 9s ease-in-out infinite; }}
  .turn {{ animation: turn 260s linear infinite; }}
  .turn-back {{ animation: turn 340s linear infinite reverse; }}
  .turn-slow {{ animation: turn 420s linear infinite; }}
  .float {{ animation: float 11s ease-in-out infinite; }}
  .landing {{ animation: glow 8s ease-in-out infinite; }}
  @keyframes turn {{ to {{ transform: rotate(360deg); }} }}
  @keyframes float {{ 50% {{ transform: translateY(-4px); }} }}
  @keyframes glow {{ 50% {{ opacity: .25; }} }}
  @keyframes ripple {{ 0%, 60%, 100% {{ stroke-opacity: var(--o); }} 20% {{ stroke: {TEAL}; stroke-opacity: .9; }} }}
  @keyframes flow {{ from {{ stroke-dashoffset: 0; opacity: 0; }} 12%, 80% {{ opacity: .8; }} to {{ stroke-dashoffset: -150; opacity: 0; }} }}
  @keyframes rise {{ 0% {{ transform: translateY(0); opacity: 0; }} 20%, 75% {{ opacity: .9; }} 100% {{ transform: translateY(var(--dy)); opacity: 0; }} }}
  @media (max-width: 560px) {{ .person {{ stroke-width: 3.5; }} .node {{ stroke-width: 4.5; }} .os-dot {{ stroke-width: 5; }} }}
  @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; }} }}
</style>
<defs>
  <linearGradient id="ground" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#eceef1"/><stop offset=".46" stop-color="#f7f8f9"/><stop offset="1" stop-color="#fff"/>
  </linearGradient>
  <radialGradient id="shade"><stop offset="0" stop-color="#0f172a" stop-opacity=".10"/><stop offset="1" stop-color="#0f172a" stop-opacity="0"/></radialGradient>
  <radialGradient id="landing"><stop offset="0" stop-color="{TEAL}" stop-opacity=".28"/><stop offset="1" stop-color="{TEAL}" stop-opacity="0"/></radialGradient>
  <clipPath id="world"><circle cx="{PCX}" cy="{PCY}" r="{PR}"/></clipPath>
</defs>
<rect width="100%" height="100%" fill="#fff"/>
<!-- the world: fills the frame like a cover photo, its horizon a little below mid-height (56%) -->
<svg y="6%" width="100%" height="100%" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice">
  <g clip-path="url(#world)">
    <rect x="-2000" y="500" width="5600" height="560" fill="url(#ground)"/>
    {arcs}
    <ellipse cx="800" cy="512" rx="330" ry="30" fill="url(#landing)" class="landing"/>
  </g>
  <circle cx="{PCX}" cy="{PCY}" r="{PR}" fill="none" stroke="#c9ced5"/>
</svg>
<!-- the stack: always whole, standing on the horizon -->
<svg x="4%" y="8%" width="92%" height="48%" viewBox="0 0 {BOX} {BASE}" preserveAspectRatio="xMidYMax meet" overflow="visible">
  {frame}
  {rising}
  {layer(Y_OS, os_body, tint='#f1f7f7')}
  {layer(Y_SERVICES, net, turn='turn-back')}
  {layer(Y_PEOPLE, crowd, turn='turn')}
</svg>
</svg>''')
