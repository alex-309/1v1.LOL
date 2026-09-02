import * as THREE from 'three';

// Blocky game-style mannequin. Returns a THREE.Group of named meshes.
export function buildMannequin(palette) {
  const body = new THREE.MeshStandardMaterial({
    name: palette.name + '_body', color: palette.body, roughness: 0.55, metalness: 0.1
  });
  const dark = new THREE.MeshStandardMaterial({
    name: palette.name + '_extremity', color: palette.dark, roughness: 0.65, metalness: 0.1
  });
  const joint = new THREE.MeshStandardMaterial({
    name: palette.name + '_joint', color: palette.joint, roughness: 0.4, metalness: 0.15
  });

  const g = new THREE.Group();
  g.name = 'mannequin_' + palette.name;

  const add = (name, geo, mat, pos, rot) => {
    const m = new THREE.Mesh(geo, mat);
    m.name = name;
    m.position.set(pos[0], pos[1], pos[2]);
    if (rot) m.rotation.set(rot[0], rot[1], rot[2]);
    m.castShadow = true;
    m.receiveShadow = true;
    g.add(m);
    return m;
  };

  const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);
  const cyl = (rt, rb, h) => new THREE.CylinderGeometry(rt, rb, h, 32);
  const ball = r => new THREE.SphereGeometry(r, 32, 20);

  // ---- torso ----
  add('chest', box(0.50, 0.32, 0.27), body, [0, 1.42, 0]);
  add('chest_bevel_top', box(0.44, 0.05, 0.22), body, [0, 1.60, 0]);
  add('abdomen', box(0.38, 0.16, 0.23), body, [0, 1.20, 0]);
  add('pelvis', box(0.44, 0.17, 0.25), body, [0, 1.05, 0]);

  // ---- head ----
  add('neck', cyl(0.075, 0.085, 0.12), dark, [0, 1.655, 0]);
  add('head', box(0.25, 0.30, 0.27), body, [0, 1.86, 0.005]);
  add('head_crown', box(0.20, 0.04, 0.22), body, [0, 2.02, 0.005]);

  // ---- arms (mirrored) ----
  for (const s of [-1, 1]) {
    const side = s < 0 ? 'L' : 'R';
    const tilt = s * 0.34; // A-pose so the arms clear the torso
    add('shoulder_' + side, ball(0.105), joint, [s * 0.275, 1.51, 0]);

    const shX = s * 0.275, shY = 1.51;
    const upperLen = 0.38, foreLen = 0.34;
    const ex = shX + Math.sin(tilt) * upperLen, ey = shY - Math.cos(tilt) * upperLen;
    add('upper_arm_' + side, cyl(0.085, 0.075, upperLen), body,
      [(shX + ex) / 2, (shY + ey) / 2, 0], [0, 0, tilt]);

    add('elbow_' + side, ball(0.078), joint, [ex, ey, 0]);
    const wx = ex + Math.sin(tilt) * foreLen, wy = ey - Math.cos(tilt) * foreLen;
    add('forearm_' + side, cyl(0.072, 0.062, foreLen), body,
      [(ex + wx) / 2, (ey + wy) / 2, 0], [0, 0, tilt]);

    add('wrist_' + side, ball(0.06), joint, [wx, wy, 0]);
    add('hand_' + side, box(0.10, 0.20, 0.12), dark, [wx + Math.sin(tilt) * 0.115, wy - 0.11, 0.005], [0, 0, tilt]);
    add('thumb_' + side, box(0.045, 0.09, 0.05), dark,
      [wx + Math.sin(tilt) * 0.10 - s * 0.065, wy - 0.07, 0.03], [0, 0, tilt]);
  }

  // ---- legs (mirrored) ----
  for (const s of [-1, 1]) {
    const side = s < 0 ? 'L' : 'R';
    const x = s * 0.125;
    add('hip_' + side, ball(0.11), joint, [x, 0.99, 0]);
    add('thigh_' + side, cyl(0.105, 0.09, 0.44), body, [x, 0.765, 0]);
    add('knee_' + side, ball(0.095), joint, [x, 0.545, 0]);
    add('shin_' + side, cyl(0.09, 0.072, 0.44), body, [x, 0.325, 0]);
    add('ankle_' + side, ball(0.068), joint, [x, 0.11, 0]);
    add('foot_' + side, box(0.155, 0.10, 0.30), dark, [x, 0.055, 0.055]);
    add('toe_' + side, box(0.14, 0.06, 0.07), dark, [x, 0.035, 0.235]);
  }

  return g;
}

export const PALETTES = {
  blue: { name: 'blue', body: 0x2f6fd0, dark: 0x1d4a94, joint: 0x2559ad },
  red: { name: 'red', body: 0xcf3b34, dark: 0x8f231f, joint: 0xac2b26 }
};
