// The case in 3D: a standard CD jewel case built in code, with the parts exactly as they print (cut out of the print
// PDF by the server) as textures. Orbit with the mouse; open and close the lid, pop the disc out and put it back. All sizes are in millimetres; the case stands on a surface, front facing +z, hinge on the left (-x).
// A modelled .glb with the same part names can replace the built case later.

import { THREE, Stage, paperMaterial, planeUV, plastic, rbox, solid } from "./stage3d.js";

// standard jewel case (142 x 125 x 10.4) and print sizes
const CASE_W = 142, CASE_H = 125, CASE_D = 10.4, BASE_D = 7.9, WALL = 1.2;
const FRONT = 120, TRAY_W = 151, TRAY_H = 118, SPINE = 6.5, LABEL = 118;
const DISC_R = 60, DISC_HOLE_R = 7.5, DISC_T = 1.2, MIRROR_IN = 16.5, MIRROR_OUT = 59.2;
const CARD_Z = 1.3;                        // the tray card's back panel, just inside the base's back plate
const TRAY_FLOOR = 2.0, TRAY_TOP = 7.2, WELL_R = 61;   // the tray: floor, top face, disc well
const DISC_Z = 3.4;                        // underside of the disc, resting on the hub's shoulder
const FLOOR_Y = -CASE_H / 2;               // the surface the case stands on
// The front insert and the disc share one centre, so the closed case shows the cover and not the disc. It sits a
// little right of the case's middle: the hinge strip on the left is wider than the lip and tabs on the right.
const DISC_X = 2;

function ring(inner, outer, depth, segments = 128) {
  const s = new THREE.Shape().absarc(0, 0, outer, 0, Math.PI * 2, false);
  s.holes.push(new THREE.Path().absarc(0, 0, inner, 0, Math.PI * 2, true));
  return new THREE.ExtrudeGeometry(s, { depth, bevelEnabled: false, curveSegments: segments });
}

// the tray's centre rosette: 12 springy teeth that grip the disc's hole
function rosette(r, depth) {
  const s = new THREE.Shape(), teeth = 12;
  for (let i = 0; i <= teeth * 2; i++) {
    const a = (i / (teeth * 2)) * Math.PI * 2, rr = i % 2 ? r * 0.72 : r;
    i ? s.lineTo(Math.cos(a) * rr, Math.sin(a) * rr) : s.moveTo(Math.cos(a) * rr, Math.sin(a) * rr);
  }
  s.holes.push(new THREE.Path().absarc(0, 0, r * 0.42, 0, Math.PI * 2, true));
  return new THREE.ExtrudeGeometry(s, { depth, bevelEnabled: true, bevelThickness: 0.3, bevelSize: 0.25, bevelSegments: 2 });
}

// the tray: a plate with the disc well cut out and two finger notches at its sides
function trayTop(w, h, depth) {
  const s = new THREE.Shape();
  s.moveTo(-w / 2, -h / 2); s.lineTo(w / 2, -h / 2); s.lineTo(w / 2, h / 2); s.lineTo(-w / 2, h / 2); s.closePath();
  // the well's outline: the disc circle merged with a finger notch on each side, traced as one simple loop (the
  // union is star-shaped around the centre, so each direction meets its edge once)
  const well = new THREE.Path(), steps = 256, notchR = 3.8;   // notches reach DISC_X + 64.8 mm: inside the 67.8 mm half-width
  for (let i = 0; i < steps; i++) {
    const a = (i / steps) * Math.PI * 2, dx = Math.cos(a), dy = Math.sin(a);
    let r = WELL_R;
    for (const cx of [-WELL_R, WELL_R]) {           // ray from the centre against the notch circle at (cx, 0)
      const b = dx * cx, disc = b * b - (cx * cx - notchR * notchR);
      if (disc >= 0 && b > 0) r = Math.max(r, b + Math.sqrt(disc));
    }
    i ? well.lineTo(DISC_X + dx * r, dy * r) : well.moveTo(DISC_X + dx * r, dy * r);
  }
  well.closePath();
  s.holes.push(well);
  return new THREE.ExtrudeGeometry(s, { depth, bevelEnabled: true, bevelThickness: 0.35, bevelSize: 0.35, bevelSegments: 3, curveSegments: 96 });
}

export class CaseViewer extends Stage {
  constructor(el, { discHole = 41 } = {}) {
    super(el, { floorY: FLOOR_Y, camera: [215, 75, 400], target: [0, 0, CASE_D / 2] });
    this.open = 0;                          // 0 closed .. 1 open (lid at 165 degrees)
    this.target = 0;
    this.discP = 0;                         // where the disc is on its way out: 0 in the tray .. 1 beside the case
    this.discTarget = 0;
    for (const part of ["front", "inside", "back", "tray"]) this.paper[part] = paperMaterial();
    // the disc label: matte print, a clear 0.06 mm above the disc's top (the log depth buffer keeps them apart)
    this.paper.disc = new THREE.MeshPhysicalMaterial({ color: 0xeeeeee, roughness: 0.7, envMapIntensity: 0.7 });
    this.build(discHole);
    this.start();
  }

  build(discHole) {
    const clear = plastic(0.12);
    const window_ = plastic(0.06);           // the lid's big flat face: thin, so the cover reads through it
    this.trayClear = plastic(0.24);
    const root = new THREE.Group();
    this.scene.add(root);

    // ---- base: back plate, walls, hinge sockets
    const base = new THREE.Group();
    root.add(base);
    const back = rbox(CASE_W, CASE_H, WALL, window_, 1.2);
    back.position.z = WALL / 2;
    base.add(back);
    for (const sy of [-1, 1]) {
      const wall = rbox(CASE_W, WALL, BASE_D, clear, 0.6);
      wall.position.set(0, sy * (CASE_H / 2 - WALL / 2), BASE_D / 2);
      base.add(wall);
    }
    const right = rbox(WALL, CASE_H - 2 * WALL, BASE_D - 1.5, clear, 0.5);
    right.position.set(CASE_W / 2 - WALL / 2, 0, (BASE_D - 1.5) / 2);
    const left = rbox(WALL * 1.6, CASE_H - 2 * WALL, BASE_D, clear, 0.6);
    left.position.set(-CASE_W / 2 + WALL * 0.8, 0, BASE_D / 2);
    base.add(right, left);
    for (const sy of [-1, 1]) {            // the base's hinge sockets, at the ends of the left edge
      const sock = new THREE.Mesh(new THREE.CylinderGeometry(1.9, 1.9, 9, 24), clear);
      sock.position.set(-CASE_W / 2, sy * (CASE_H / 2 - 4.5), BASE_D);
      sock.renderOrder = 2;
      base.add(sock);
    }

    // ---- tray card: back panel (printed side out, read from behind), spines folded into the sides, under-tray image
    const cardBack = solid(new THREE.Mesh(planeUV(TRAY_W - 2 * SPINE, TRAY_H, SPINE / TRAY_W, 1 - SPINE / TRAY_W), this.paper.back));
    cardBack.rotation.y = Math.PI;
    cardBack.position.z = CARD_Z;
    const spineL = solid(new THREE.Mesh(planeUV(SPINE, TRAY_H, 0, SPINE / TRAY_W), this.paper.back));
    spineL.rotation.y = Math.PI / 2;
    spineL.position.set((TRAY_W - 2 * SPINE) / 2, 0, CARD_Z + SPINE / 2);
    const spineR = solid(new THREE.Mesh(planeUV(SPINE, TRAY_H, 1 - SPINE / TRAY_W, 1), this.paper.back));
    spineR.rotation.y = -Math.PI / 2;
    spineR.position.set(-(TRAY_W - 2 * SPINE) / 2, 0, CARD_Z + SPINE / 2);
    const under = solid(new THREE.Mesh(planeUV(TRAY_W - 2 * SPINE, TRAY_H), this.paper.tray));
    under.position.z = CARD_Z + 0.15;
    base.add(cardBack, spineL, spineR, under);

    // ---- the tray: floor, top plate with the disc well and finger notches, centre rosette
    this.tray = new THREE.Group();
    const tw = TRAY_W - 2 * SPINE - 1.5, th = TRAY_H - 1;
    const floor = rbox(tw, th, 0.8, this.trayClear, 0.3);
    floor.position.z = TRAY_FLOOR + 0.4;
    const top = new THREE.Mesh(trayTop(tw - 0.8, th - 0.8, TRAY_TOP - TRAY_FLOOR - 1.5), this.trayClear);
    top.position.z = TRAY_FLOOR + 0.8;
    top.renderOrder = 2;
    const shoulder = new THREE.Mesh(ring(DISC_HOLE_R - 0.2, 12, DISC_Z - TRAY_FLOOR - 0.8), this.trayClear);
    shoulder.position.set(DISC_X, 0, TRAY_FLOOR + 0.8);
    shoulder.renderOrder = 2;
    const hub = new THREE.Mesh(rosette(DISC_HOLE_R - 0.15, 2.4), this.trayClear);
    hub.position.set(DISC_X, 0, DISC_Z);
    hub.renderOrder = 2;
    this.tray.add(floor, top, shoulder, hub);
    base.add(this.tray);

    // ---- the disc: clear polycarbonate, the mirror layer (with a rainbow sheen), the printed label on top
    this.disc = new THREE.Group();
    const poly = new THREE.MeshPhysicalMaterial({ color: 0xf4f6f8, roughness: 0.05, transparent: true, opacity: 0.35,
      clearcoat: 1, depthWrite: false });
    const mirror = new THREE.MeshPhysicalMaterial({ color: 0xd6dade, metalness: 1, roughness: 0.14, iridescence: 1,
      iridescenceIOR: 1.8, iridescenceThicknessRange: [250, 800], envMapIntensity: 1.3 });
    const body = new THREE.Mesh(ring(DISC_HOLE_R, DISC_R, DISC_T), poly);
    body.renderOrder = 3;
    const data = solid(new THREE.Mesh(ring(MIRROR_IN, MIRROR_OUT, DISC_T * 0.4), mirror));
    data.position.z = DISC_T * 0.3;
    const label = solid(new THREE.Mesh(new THREE.RingGeometry(discHole / 2, LABEL / 2, 128, 1), this.paper.disc));
    label.position.z = DISC_T + 0.06;
    this.discLabel = label;
    this.disc.add(body, data, label);
    this.disc.position.set(DISC_X, 0, DISC_Z);
    base.add(this.disc);

    // ---- the lid: hinged on the left edge; the front insert sits under it, held by two tabs, its inside facing the disc
    this.lid = new THREE.Group();
    this.lid.position.set(-CASE_W / 2, 0, BASE_D);
    root.add(this.lid);
    const lidD = CASE_D - BASE_D;
    const lidTop = rbox(CASE_W, CASE_H, 1.3, window_, 1.2);
    lidTop.position.set(CASE_W / 2, 0, lidD - 0.65);
    this.lid.add(lidTop);
    for (const sy of [-1, 1]) {
      const lip = rbox(CASE_W - 4, WALL, lidD, clear, 0.5);
      lip.position.set(CASE_W / 2 + 1, sy * (CASE_H / 2 - WALL / 2), lidD / 2);
      this.lid.add(lip);
      const tab = rbox(7, 3, 0.9, clear, 0.35);   // the insert tabs on the open side
      tab.position.set(CASE_W / 2 + DISC_X + FRONT / 2 + 1, sy * (FRONT / 2 - 14), lidD - 1.75);   // over the insert's right edge
      this.lid.add(tab);
      const knuckle = new THREE.Mesh(new THREE.CylinderGeometry(1.6, 1.6, 7, 24), clear);
      knuckle.position.set(0, sy * (CASE_H / 2 - 13), 0.2);
      knuckle.renderOrder = 2;
      this.lid.add(knuckle);
    }
    const edge = rbox(WALL, CASE_H - 2 * WALL, lidD, clear, 0.5);
    edge.position.set(CASE_W - WALL / 2, 0, lidD / 2);
    this.lid.add(edge);
    const insertX = CASE_W / 2 + DISC_X;               // in the lid's frame (its origin is the hinge)
    const front = solid(new THREE.Mesh(planeUV(FRONT, FRONT), this.paper.front));
    front.position.set(insertX, 0, lidD - 1.32);
    const inside = solid(new THREE.Mesh(planeUV(FRONT, FRONT), this.paper.inside));
    inside.rotation.y = Math.PI;
    inside.position.set(insertX, 0, lidD - 1.5);
    this.lid.add(front, inside);
  }

  // ---- what is shown -------------------------------------------------------------------------------------------------
  setDiscHole(mm) {
    if (this.discHoleMM === mm) return;
    this.discHoleMM = mm;
    this.discLabel.geometry.dispose();
    this.discLabel.geometry = new THREE.RingGeometry(mm / 2, LABEL / 2, 128, 1);
  }

  setOpen(open) { this.target = open ? 1 : 0; }
  // the disc only comes out (or goes back) through an open lid: the caller opens it, and the disc waits for it
  setDiscOut(out) { this.discTarget = out ? 1 : 0; }

  // where the disc is at `p`: 0 in the tray, 0.4 popped straight out in front of the case, 1 beside it on the right
  discPose(p) {
    const d = this.disc, popZ = CASE_D + 14, outX = CASE_W / 2 + DISC_R + 14;
    const smooth = (x) => x * x * (3 - 2 * x);
    const pop = smooth(Math.min(1, p / 0.4)), slide = smooth(Math.max(0, (p - 0.4) / 0.6));
    d.position.set(DISC_X + (outX - DISC_X) * slide, 0, DISC_Z + (popZ - DISC_Z) * pop);
    d.rotation.set(0, 0, 0);
  }

  // ---- loop ------------------------------------------------------------------------------------------------------
  update(dt) {
    const ease = (k) => Math.min(1, dt * k);
    // the lid stays open while the disc is on its way in or out, and closes once it has landed
    const moving = this.discTarget !== this.discP;
    this.open += ((moving ? 1 : this.target) - this.open) * ease(6);
    this.lid.rotation.y = -this.open * THREE.MathUtils.degToRad(165);
    // the disc: moves along its path at a steady pace, once the lid is open enough to let it through
    const going = this.discTarget - this.discP;
    if (going && this.open > 0.85) this.discP = Math.max(0, Math.min(1, this.discP + Math.sign(going) * dt / 1.1));
    this.discPose(this.discP);
    const outX = CASE_W / 2 + DISC_R + 14;
    // keep everything in view: an open lid lies left of the base, a disc taken out sits right of it. Pan to the
    // middle and dolly out as the scene widens, keeping any orbit and zoom you chose.
    const out = (this.disc.position.x - DISC_X) / (outX - DISC_X);
    this.frame(-this.open * CASE_W / 2 + out * (DISC_R + 30), 1 + 0.5 * this.open + 0.5 * out);
  }
}
