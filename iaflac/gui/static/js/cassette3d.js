// The cassette in 3D: a clear cassette case standing upright, with the J-card exactly as it prints (cut out of its PDF
// by the server) folded inside it: the front behind the lid, the spine down the hinge edge, the tracklist flap behind
// the tape. Open the lid (it swings out on the spine edge) and take the tape out. All sizes in millimetres; the front
// faces +z, the spine is on the left (-x).

import { THREE, Stage, paperMaterial, planeUV, plastic, rbox, solid } from "./stage3d.js";

// a clear cassette case, about 69.6 x 109 x 16.8 mm, and the compact cassette in it (100.4 x 63.8 x 12 mm)
const CASE_W = 69.6, CASE_H = 109, CASE_D = 16.8, WALL = 1.2, BASE_D = 6;
const TAPE_W = 63.8, TAPE_H = 100.4, TAPE_D = 12;
// the J-card, as iaflac/cassette.py prints it
const J_H = 101.6, J_FLAP = 27.0, J_SPINE = 12.7, J_FRONT = 65.1;
const FLOOR_Y = -CASE_H / 2;
const CARD_X = -CASE_W / 2 + WALL + 0.1;          // the fold between the spine and the front, against the spine wall
const CARD_FRONT_Z = CASE_D - WALL - 0.15;        // the front panel, just behind the lid's face
const CARD_BACK_Z = CARD_FRONT_Z - J_SPINE;       // the fold between the spine and the flap
const TAPE_X = CARD_X + J_FRONT / 2;              // the tape sits behind the front panel
const TAPE_Z = CARD_BACK_Z + 0.2 + TAPE_D / 2;    // between the flap and the front panel

// the tape's label: cream card with the album and artist, a cut-out window over the tape (drawn landscape, as on a tape)
function labelTexture(artist, album) {
  const W = 890, H = 540, c = document.createElement("canvas");
  c.width = W; c.height = H;
  const g = c.getContext("2d");
  g.fillStyle = "#f2ead8";
  g.fillRect(0, 0, W, H);
  g.fillStyle = "#c9403f";
  g.fillRect(0, 150, W, 10);                                // the classic coloured stripe
  g.fillStyle = "#1b1b1f";
  g.font = "bold 74px Helvetica, Arial, sans-serif";
  g.fillText("A", 34, 108);
  const fit = (text, size, weight, max) => {
    let s = size;
    do { g.font = `${weight} ${s}px Helvetica, Arial, sans-serif`; s -= 2; } while (g.measureText(text).width > max && s > 18);
    return text;
  };
  g.fillText(fit(album || "", 58, "bold", W - 170), 124, 82);
  g.fillStyle = "#4a4a50";
  g.fillText(fit(artist || "", 38, "normal", W - 170), 124, 128);
  g.strokeStyle = "rgba(0,0,0,.18)";
  g.lineWidth = 3;
  for (const y of [200, 236]) { g.beginPath(); g.moveTo(40, y); g.lineTo(W - 40, y); g.stroke(); }
  // the window over the tape and its two hubs
  const wx = W / 2 - 230, wy = 270, ww = 460, wh = 140, r = 26;
  g.globalCompositeOperation = "destination-out";
  g.beginPath();
  g.roundRect(wx, wy, ww, wh, r);
  g.fill();
  g.globalCompositeOperation = "source-over";
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

export class CassetteViewer extends Stage {
  constructor(el) {
    super(el, { floorY: FLOOR_Y, camera: [190, 65, 345], target: [0, 0, CASE_D / 2], minDistance: 110 });
    this.open = 0;                                  // 0 closed .. 1 open (lid at 150 degrees)
    this.target = 0;
    this.tapeP = 0;                                 // 0 in the case .. 1 beside it
    this.tapeTarget = 0;
    for (const part of ["front", "spine", "flap", "inside"]) this.paper[part] = paperMaterial();
    this.build();
    this.start();
  }

  build() {
    const clear = plastic(0.12), window_ = plastic(0.06);
    const root = new THREE.Group();
    this.scene.add(root);

    // ---- base: the back, and walls on three sides (the spine side belongs to the lid)
    const base = new THREE.Group();
    root.add(base);
    const back = rbox(CASE_W, CASE_H, WALL, window_, 1.4);
    back.position.z = WALL / 2;
    base.add(back);
    for (const sy of [-1, 1]) {
      const wall = rbox(CASE_W - 2 * WALL, WALL, BASE_D, clear, 0.5);
      wall.position.set(WALL, sy * (CASE_H / 2 - WALL / 2), BASE_D / 2);
      base.add(wall);
    }
    const right = rbox(WALL, CASE_H - 2 * WALL, BASE_D, clear, 0.5);
    right.position.set(CASE_W / 2 - WALL / 2, 0, BASE_D / 2);
    base.add(right);
    for (const sy of [-1, 1]) {                      // the two spindles the tape's hubs sit on
      const post = new THREE.Mesh(new THREE.CylinderGeometry(3.6, 3.6, TAPE_Z - WALL, 20), clear);
      post.rotation.x = Math.PI / 2;
      post.position.set(TAPE_X, sy * 21.25, WALL + (TAPE_Z - WALL) / 2);
      post.renderOrder = 2;
      base.add(post);
    }

    // ---- the tape: a black shell, the label with its window, the tape packs and hubs seen through it
    this.tape = new THREE.Group();
    const shell = solid(rbox(TAPE_W, TAPE_H, TAPE_D,
      new THREE.MeshPhysicalMaterial({ color: 0x18181b, roughness: 0.38, clearcoat: 0.35, clearcoatRoughness: 0.3 }), 1.6));
    shell.renderOrder = 0;
    this.tape.add(shell);
    const face = TAPE_D / 2;
    // the label's window sits 7 mm off its centre (towards its lower edge, which faces +x once the label is turned)
    const WIN_X = -7;
    const glass = new THREE.Mesh(new THREE.PlaneGeometry(14.5, 48), new THREE.MeshPhysicalMaterial({ color: 0x3a3532,
      roughness: 0.15, clearcoat: 1 }));
    glass.position.set(WIN_X, 0, face + 0.04);
    this.tape.add(glass);
    for (const [sy, pack] of [[1, 15.5], [-1, 10.5]]) {        // more tape wound on one side than the other
      const tapeRoll = new THREE.Mesh(new THREE.CircleGeometry(pack, 48),          // the label hides all but the window
        new THREE.MeshStandardMaterial({ color: 0x3b2418, roughness: 0.55 }));
      tapeRoll.position.set(WIN_X, sy * 21.25, face + 0.08);
      const hub = new THREE.Mesh(new THREE.RingGeometry(2.6, 5.4, 6, 1),
        new THREE.MeshStandardMaterial({ color: 0xf4f4f2, roughness: 0.4 }));
      hub.position.set(WIN_X, sy * 21.25, face + 0.12);
      this.tape.add(tapeRoll, hub);
    }
    this.labelMat = new THREE.MeshStandardMaterial({ map: labelTexture("", ""), transparent: true, alphaTest: 0.5, roughness: 0.75 });
    const label = new THREE.Mesh(new THREE.PlaneGeometry(89, 54), this.labelMat);
    label.rotation.z = -Math.PI / 2;                            // a tape label reads along the tape's length
    label.position.z = face + 0.2;
    this.tape.add(label);
    this.tape.position.set(TAPE_X, 0, TAPE_Z);
    base.add(this.tape);

    // ---- the lid: face, spine wall and lips, hinged on the back of the spine edge; the J-card folds inside it
    this.lid = new THREE.Group();
    this.lid.position.set(-CASE_W / 2, 0, 0);
    root.add(this.lid);
    const at = (m, x, y, z) => { m.position.set(x + CASE_W / 2, y, z); return m; };   // case coords -> lid frame
    this.lid.add(at(rbox(CASE_W, CASE_H, WALL, window_, 1.4), 0, 0, CASE_D - WALL / 2));
    this.lid.add(at(rbox(WALL, CASE_H, CASE_D, clear, 0.6), -CASE_W / 2 + WALL / 2, 0, CASE_D / 2));
    for (const sy of [-1, 1]) {
      this.lid.add(at(rbox(CASE_W - WALL, WALL, CASE_D - BASE_D, clear, 0.5), WALL / 2, sy * (CASE_H / 2 - WALL / 2), (CASE_D + BASE_D) / 2));
    }
    this.lid.add(at(rbox(WALL, CASE_H - 2 * WALL, CASE_D - BASE_D, clear, 0.5), CASE_W / 2 - WALL / 2, 0, (CASE_D + BASE_D) / 2));
    // the J-card: front (printed side out), spine (outward, on the hinge edge), flap (printed side out, through the back)
    const front = solid(new THREE.Mesh(planeUV(J_FRONT, J_H), this.paper.front));
    this.lid.add(at(front, CARD_X + J_FRONT / 2, 0, CARD_FRONT_Z));
    const spine = solid(new THREE.Mesh(planeUV(J_SPINE, J_H), this.paper.spine));
    spine.rotation.y = -Math.PI / 2;                            // faces -x; its right edge (the front fold) at the front
    this.lid.add(at(spine, CARD_X, 0, CARD_BACK_Z + J_SPINE / 2));
    const flap = solid(new THREE.Mesh(planeUV(J_FLAP, J_H), this.paper.flap));
    flap.rotation.y = Math.PI;                                  // faces -z; its right edge (the spine fold) at the spine
    this.lid.add(at(flap, CARD_X + J_FLAP / 2, 0, CARD_BACK_Z));
    // its inside (page 2, which runs front | spine | flap from the left as printed), facing into the case
    const W = J_FRONT + J_SPINE + J_FLAP, u = (mm) => mm / W;
    const inFront = solid(new THREE.Mesh(planeUV(J_FRONT, J_H, 0, u(J_FRONT)), this.paper.inside));
    inFront.rotation.y = Math.PI;
    this.lid.add(at(inFront, CARD_X + J_FRONT / 2, 0, CARD_FRONT_Z - 0.12));
    const inSpine = solid(new THREE.Mesh(planeUV(J_SPINE, J_H, u(J_FRONT), u(J_FRONT + J_SPINE)), this.paper.inside));
    inSpine.rotation.y = Math.PI / 2;
    this.lid.add(at(inSpine, CARD_X + 0.12, 0, CARD_BACK_Z + J_SPINE / 2));
    const inFlap = solid(new THREE.Mesh(planeUV(J_FLAP, J_H, u(J_FRONT + J_SPINE), 1), this.paper.inside));
    this.lid.add(at(inFlap, CARD_X + J_FLAP / 2, 0, CARD_BACK_Z + 0.12));
  }

  setLabel(artist, album) {
    const key = `${artist}\u0000${album}`;
    if (key === this.labelKey) return;
    this.labelKey = key;
    this.labelMat.map.dispose();
    this.labelMat.map = labelTexture(artist, album);
    this.labelMat.needsUpdate = true;
  }

  setOpen(open) { this.target = open ? 1 : 0; }
  // the tape only comes out (or goes back) through an open lid: the caller opens it, and the tape waits for it
  setTapeOut(out) { this.tapeTarget = out ? 1 : 0; }

  // 0 in the case, 0.4 popped straight out in front of it, 1 beside it on the right
  tapePose(p) {
    const popZ = CASE_D + 16, outX = CASE_W / 2 + TAPE_W / 2 + 14;
    const smooth = (x) => x * x * (3 - 2 * x);
    const pop = smooth(Math.min(1, p / 0.4)), slide = smooth(Math.max(0, (p - 0.4) / 0.6));
    this.tape.position.set(TAPE_X + (outX - TAPE_X) * slide, 0, TAPE_Z + (popZ - TAPE_Z) * pop);
  }

  update(dt) {
    const ease = (k) => Math.min(1, dt * k);
    const moving = this.tapeTarget !== this.tapeP;            // the lid stays open while the tape moves
    this.open += ((moving ? 1 : this.target) - this.open) * ease(6);
    this.lid.rotation.y = -this.open * THREE.MathUtils.degToRad(150);
    const going = this.tapeTarget - this.tapeP;
    if (going && this.open > 0.85) this.tapeP = Math.max(0, Math.min(1, this.tapeP + Math.sign(going) * dt / 1.1));
    this.tapePose(this.tapeP);
    const outX = CASE_W / 2 + TAPE_W / 2 + 14;
    const out = (this.tape.position.x - TAPE_X) / (outX - TAPE_X);
    this.frame(-this.open * CASE_W / 2 + out * (TAPE_W / 2 + 20), 1 + 0.5 * this.open + 0.5 * out);
  }
}
