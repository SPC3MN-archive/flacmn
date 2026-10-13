// What every 3D mockup shares: the renderer, a soft studio light with shadows on a surface, an orbit camera, the
// printed parts as paper materials, clear-plastic materials, and keeping the scene in frame as it changes.
// A mockup extends Stage, builds its model in build() and animates it in update(dt).

import * as THREE from "three";
import { OrbitControls } from "/static/vendor/three/OrbitControls.js";
import { RoomEnvironment } from "/static/vendor/three/RoomEnvironment.js";
import { RoundedBoxGeometry } from "/static/vendor/three/RoundedBoxGeometry.js";

export { THREE };

// a plane whose texture is a horizontal slice [u0, u1] of the printed part
export function planeUV(w, h, u0 = 0, u1 = 1) {
  const g = new THREE.PlaneGeometry(w, h);
  const uv = g.attributes.uv;
  for (let i = 0; i < uv.count; i++) uv.setX(i, u0 + (u1 - u0) * uv.getX(i));
  return g;
}

// clear polystyrene: mostly see-through, with bright reflections on the rounded edges
export function plastic(opacity) {
  return new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0, roughness: 0.04, transparent: true, opacity,
    depthWrite: false, clearcoat: 1, clearcoatRoughness: 0.02, envMapIntensity: 1.1, side: THREE.DoubleSide });
}

export function rbox(w, h, d, mat, r = 0.5) {
  const m = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, Math.min(r, d / 2 - 0.01, w / 2 - 0.01, h / 2 - 0.01)), mat);
  m.renderOrder = 2;
  return m;
}

export function solid(m) { m.castShadow = true; m.receiveShadow = true; return m; }

export function paperMaterial() {
  return new THREE.MeshPhysicalMaterial({ color: 0xeeeeee, roughness: 0.62, sheen: 0.3, sheenRoughness: 0.8, envMapIntensity: 0.7 });
}

export class Stage {
  /** floorY: the surface the model stands on; camera: start position; target: what it looks at. */
  constructor(el, { floorY, camera, target, minDistance = 140 }) {
    this.el = el;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, logarithmicDepthBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    el.append(this.renderer.domElement);

    this.scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.03).texture;
    pmrem.dispose();
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(-90, 260, 220);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.radius = 6;
    key.shadow.bias = -0.0004;
    Object.assign(key.shadow.camera, { left: -260, right: 260, top: 260, bottom: -260, near: 50, far: 800 });
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0xdfe6ff, 0.6);
    rim.position.set(200, 80, -200);
    this.scene.add(rim);

    const ground = new THREE.Mesh(new THREE.PlaneGeometry(1600, 1600), new THREE.ShadowMaterial({ opacity: 0.16 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = floorY - 0.01;
    ground.receiveShadow = true;
    this.scene.add(ground);

    this.camera = new THREE.PerspectiveCamera(28, 1, 10, 3000);
    this.camera.position.set(...camera);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(...target);
    this.controls.enableDamping = true;
    this.controls.minDistance = minDistance;
    this.controls.maxDistance = 900;
    this.controls.maxPolarAngle = Math.PI / 2 + 0.05;     // not far below the surface
    this.baseTargetX = target[0];
    this.reach = 1;

    this.loader = new THREE.TextureLoader();
    this.paper = {};                                       // part -> material, given its printed texture later
  }

  // call at the end of the subclass constructor, once the model is built
  start() {
    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(this.el);
    this.fit();
    this.clock = new THREE.Clock();
    this.renderer.setAnimationLoop(() => this.tick());
  }

  setTextures(urls) {
    for (const [part, url] of Object.entries(urls)) {
      const mat = this.paper[part];
      if (!mat) continue;
      this.loader.load(url, (tex) => {
        tex.colorSpace = THREE.SRGBColorSpace;
        tex.anisotropy = this.renderer.capabilities.getMaxAnisotropy();
        if (mat.map) mat.map.dispose();
        mat.map = tex;
        mat.color.set(0xffffff);
        mat.needsUpdate = true;
      });
    }
  }

  // keep the scene in view as it widens: pan the target to x and dolly out by `reach`, keeping any orbit and zoom
  frame(x, reach) {
    const shift = this.baseTargetX + x - this.controls.target.x;
    this.controls.target.x += shift;
    this.camera.position.x += shift;
    const offset = this.camera.position.clone().sub(this.controls.target);
    this.camera.position.copy(this.controls.target).add(offset.multiplyScalar(reach / this.reach));
    this.reach = reach;
  }

  update() {}                                              // the subclass animates its model here

  fit() {
    const { clientWidth: w, clientHeight: h } = this.el;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  tick() {
    this.update(Math.min(this.clock.getDelta(), 0.05));
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    this.resize?.disconnect();
    this.controls.dispose();
    this.scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      const mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
      for (const m of mats) { if (m.map) m.map.dispose(); m.dispose(); }
    });
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
