/* ============================================================
   viz/gl3d.js — 3D 星空（WebGL / Three.js r160 本地库，v0.3.3）
   架构沿用评估原型 demo-3d.html（宿主认可其架构），只把"塑料球"换成**玻璃球**并补光追要素：
     · 玻璃：MeshPhysicalMaterial + transmission（真实透射折射）+ ior 1.46 + clearcoat
             + attenuation 体色 + 粗糙度分档（枢纽=清透水晶 / 叶节点=哑光磨砂）
     · 光追要素（无后处理，靠材质与叠加层实现）：
         ① 环境反射/折射：程序化星云 equirect → PMREM 生成 envMap（玻璃能"照见"星空）
         ② 透射：球体后的星尘/其他球体透过玻璃可见（真实折射，非贴图假象）
         ③ 泛光：枢纽节点叠加加法混合的柔光精灵（模拟镜头/人眼散射）
         ④ 光流：血缘边上有沿着连线流动的光点（方向=演化/合并真实方向）
         ⑤ 雾 + ACES 色调映射：远景自然衰减，高光滚降，避免"过曝塑料感"
   生命周期：动态 import 懒加载（1.27MB 不拖首屏）；切走视图 stop() 停 rAF；
             降级：无 WebGL / animLevel=low / 帧率不足 → 静止单帧渲染或退回平铺视图。
   ============================================================ */
'use strict';

var GL3D = {
  three: null, renderer: null, scene: null, camera: null, canvas: null, host: null,
  group: null, edgeLines: null, dust: null, flow: null, glows: [],
  nodes: [], links: [], byId: {}, raf: 0, active: false, ready: false, loading: false,
  mode: 'sphere', rotate: true, hover: null, sel: null, spin: { x: 0, y: 0 }, _drag: null,
  _lastT: 0, _paused: 0, _labels: [], _loop: null, entrance: null, lightOn: true,
  dustCount: 1400, glowOn: true, W: 0, H: 0, dpr: 1,

  /* ── 能力探测：内核支持 ESM 动态 import 且 WebGL 可用 ── */
  supported: function () {
    if (typeof document === 'undefined') return false;
    if (typeof Symbol === 'function' && typeof Promise !== 'function') return false;
    try {
      var c = document.createElement('canvas');
      return !!(c.getContext && (c.getContext('webgl2') || c.getContext('webgl')));
    } catch (e) { return false; }
  },
  /* ─ 懒加载 Three.js（失败返回 false，调用方回退平铺视图） ─ */
  load: function () {
    var self = this;
    if (self._lib) return Promise.resolve(true);
    if (self._loading) return self._loading;
    if (!self.supported()) return Promise.resolve(false);
    self._loading = import('/static/lib/three.module.js').then(function (T) {
      self.three = T; self._lib = true; self._loading = null; return true;
    }).catch(function () { self._loading = null; return false; });
    return self._loading;
  },

  /* 卸载场景与 WebGL 上下文（切走视图/退出 3D 模式时调用；库引用保留，无需重新下载） */
  dispose: function () {
    this.stop();
    try { if (this.disposeGraph) this.disposeGraph.call(this); } catch (e) { }
    try {
      if (this.dust) { this.dust.geometry.dispose(); this.dust.material.dispose(); this.dust = null; }
    } catch (e) { }
    try {
      if (this.renderer) { this.renderer.dispose(); if (this.renderer.forceContextLoss) this.renderer.forceContextLoss(); }
    } catch (e) { }
    if (this.canvas && this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
    if (this._labelBox && this._labelBox.parentNode) this._labelBox.parentNode.removeChild(this._labelBox);
    this.renderer = null; this.scene = null; this.camera = null; this.canvas = null;
    this._labelBox = null; this._labels = []; this.ready = false; this.host = null;
    this.hover = null; this.sel = null; this.hood = null; this.entrance = null; this._bound = false;
    if (this._upHandler) { window.removeEventListener('pointerup', this._upHandler); this._upHandler = null; }
  },

  /* ── 程序化星云环境贴图（equirect → PMREM）：玻璃"照见"的东西
       环境亮度直接决定玻璃的观感——太暗玻璃会变成黑洞，所以星云与"窗光"都给足 ── */
  makeEnv: function (T) {
    var c = document.createElement('canvas');
    c.width = 1024; c.height = 512;
    var g = c.getContext('2d');
    var grd = g.createLinearGradient(0, 0, 0, 512);
    grd.addColorStop(0, '#1b2c46'); grd.addColorStop(.42, '#0e1826'); grd.addColorStop(1, '#070c14');
    g.fillStyle = grd; g.fillRect(0, 0, 1024, 512);
    var blob = function (x, y, r, col) {
      var rg = g.createRadialGradient(x, y, 0, x, y, r);
      rg.addColorStop(0, col); rg.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = rg; g.fillRect(x - r, y - r, r * 2, r * 2);
    };
    blob(280, 150, 300, 'rgba(96,200,255,.95)');
    blob(760, 340, 280, 'rgba(168,132,255,.8)');
    blob(560, 90, 190, 'rgba(255,224,190,.75)');
    blob(120, 400, 200, 'rgba(90,220,200,.55)');
    // "窗光"高光点：玻璃上那些细亮反光来自这里（越亮越像光追）
    for (var i = 0; i < 40; i++) {
      var x = Math.random() * 1024, y = Math.random() * 512, r = 2 + Math.random() * 7;
      g.fillStyle = 'rgba(255,255,255,' + (0.5 + Math.random() * 0.5).toFixed(2) + ')';
      g.beginPath(); g.arc(x, y, r, 0, 6.2832); g.fill();
    }
    var tex = new T.CanvasTexture(c);
    tex.mapping = T.EquirectangularReflectionMapping;
    tex.colorSpace = T.SRGBColorSpace;
    return tex;
  },
  /* 圆形柔光点贴图（星尘 / 泛光精灵共用） */
  makeDot: function (T, hard) {
    var c = document.createElement('canvas');
    c.width = c.height = 64;
    var g = c.getContext('2d');
    var rg = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    rg.addColorStop(0, 'rgba(255,255,255,1)');
    rg.addColorStop(hard ? .2 : .35, 'rgba(255,255,255,' + (hard ? .9 : .55) + ')');
    rg.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = rg; g.beginPath(); g.arc(32, 32, 32, 0, 6.2832); g.fill();
    var tex = new T.CanvasTexture(c);
    tex.colorSpace = T.SRGBColorSpace;
    return tex;
  },

  /* ── 建立场景（一次） ── */
  build: function () {
    var T = this.three;
    var host = this.host;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'gl3d-canvas';
    host.appendChild(this.canvas);
    var renderer = this.renderer = new T.WebGLRenderer({ canvas: this.canvas, antialias: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.toneMapping = T.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.02;
    renderer.transmissionResolutionScale = 0.5;   // 透射缓冲减半：玻璃折射照旧，但省一半带宽
    var scene = this.scene = new T.Scene();
    scene.fog = new T.FogExp2(0x060a12, 0.00032);
    var camera = this.camera = new T.PerspectiveCamera(46, 1, 1, 6000);
    camera.position.set(0, 40, 1000);
    // 环境贴图（光追的"环境"）：玻璃反射/折射星空
    var pmrem = new T.PMREMGenerator(renderer);
    pmrem.compileEquirectangularShader();
    scene.environment = pmrem.fromEquirectangular(this.makeEnv(T)).texture;
    pmrem.dispose();
    // 灯：冷色主光 + 暖色轮廓光（双光源 → 玻璃上两处高光，"高级感"的来源）
    scene.add(new T.AmbientLight(0xbcd2e8, 1.05));
    var key = new T.PointLight(0xcdf3ff, 3.4, 5200); key.position.set(460, 360, 620); scene.add(key);
    var rim = new T.PointLight(0xffcf9a, 1.7, 4200); rim.position.set(-520, -300, -460); scene.add(rim);
    var fill = new T.DirectionalLight(0xdcecff, 0.85); fill.position.set(-1, 1.4, 0.8); scene.add(fill);
    // 背景星尘
    var dn = this.dustCount;
    var dg = new T.BufferGeometry();
    var dp = new Float32Array(dn * 3);
    for (var i = 0; i < dn; i++) {
      var r = 520 + Math.random() * 1300, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
      dp[i * 3] = r * Math.sin(ph) * Math.cos(th);
      dp[i * 3 + 1] = r * Math.sin(ph) * Math.sin(th);
      dp[i * 3 + 2] = r * Math.cos(ph);
    }
    dg.setAttribute('position', new T.BufferAttribute(dp, 3));
    this.dust = new T.Points(dg, new T.PointsMaterial({
      color: 0xd6ebff, size: 7, sizeAttenuation: true, transparent: true, opacity: .85,
      map: this.makeDot(T, true), depthWrite: false, blending: T.AdditiveBlending,
    }));
    scene.add(this.dust);
    this.group = new T.Group();
    scene.add(this.group);
    this._dot = this.makeDot(T, false);
    this._dotHard = this.makeDot(T, true);
    this.ready = true;
    this.glows = [];
    this.disposeGraph = function () {
      var g = this.group;
      while (g.children.length) {
        var o = g.children.pop();
        if (o.geometry) o.geometry.dispose();
        if (o.material) { if (o.material.map && o.material.map !== this._dot) o.material.map = null; o.material.dispose(); }
      }
      this.nodes = []; this.links = []; this.byId = {}; this.glows = []; this.flow = null; this.edgeLines = null;
    };
  },

  resize: function () {
    if (!this.renderer || !this.host) return;
    var w = this.host.clientWidth || 900, h = this.host.clientHeight || 600;
    this.W = w; this.H = h;
    this.renderer.setSize(w, h, false);
    this.canvas.style.width = '100%'; this.canvas.style.height = '100%';
    this.camera.aspect = w / Math.max(1, h);
    this.camera.updateProjectionMatrix();
  },

  /* ── 挂载图数据（spec = graphSpec()：{nodes, links}） ── */
  mount: function (spec, cats, catColors) {
    var T = this.three, self = this;
    this.disposeGraph.call(this);
    var group = this.group;
    var n = spec.nodes.length || 1;
    // 半径：像素尺度直接进 3D（相机 z=1000，视野 46° → 满屏高约 850 世界单位）
    var base = 13;
    spec.nodes.forEach(function (nd, i) {
      var r = base + Math.min(16, Math.sqrt(Math.max(0, (nd.skill && nd.skill.indeg) || 0)) * 4.2);
      if (nd.cand) r = 11;
      var col = new T.Color(nd.ci >= 0 && catColors[nd.ci] ? catColors[nd.ci].base : '#c3aef0');
      var hub = (nd.skill && nd.skill.indeg >= 2);
      // ── 玻璃：透射 + 环境反射 + 内发光 + 双光源高光；枢纽=清透水晶，叶节点=哑光磨砂
      //    透射不要给满（1.0）——身后是深空，满透射会让球体变成黑洞；
      //    保留体色 + 内发光，才有"宝石/厚玻璃"的高级感。
      var mat = new T.MeshPhysicalMaterial({
        color: col, metalness: 0, roughness: hub ? 0.055 : 0.22,
        transmission: nd.cand ? 0.5 : (hub ? 0.82 : 0.68),
        thickness: r * (hub ? 1.4 : 0.9),
        ior: 1.5, clearcoat: 1, clearcoatRoughness: hub ? 0.04 : 0.18,
        envMapIntensity: hub ? 3.2 : 2.2,
        attenuationColor: col, attenuationDistance: hub ? 420 : 240,
        emissive: col, emissiveIntensity: hub ? 0.32 : 0.2,
        specularIntensity: 1, transparent: true, opacity: 1,
      });
      var mesh = new T.Mesh(new T.SphereGeometry(r, 30, 22), mat);
      mesh.position.copy(self.layoutPos(i, n));
      mesh.userData = { nd: nd, r: r, base: col.clone(), hub: hub, jitter: Math.random() * 6.2832 };
      nd.mesh = mesh; nd.r = r;
      group.add(mesh);
      // 泛光精灵（光线在眼里/镜头里散射 → 高光的"光晕"）
      if (hub) {
        var sp = new T.Sprite(new T.SpriteMaterial({
          map: self._dot, color: col, blending: T.AdditiveBlending,
          transparent: true, depthWrite: false, opacity: .34,
        }));
        sp.scale.setScalar(r * 7);
        sp.position.copy(mesh.position);
        group.add(sp);
        self.glows.push({ s: sp, nd: nd });
        nd.glow = sp;
      }
      self.byId[nd.id] = nd;
    });
    this.nodes = spec.nodes;
    this.links = spec.links;
    this.ensureLabels();
    this.buildEdges();
    this.entrance = { t0: performance.now() };
    this.updateLabels(true);
  },

  /* 球面均匀布点（Fibonacci）——原型沿用 */
  fib: function (i, n, R) {
    var off = 2 / n, inc = Math.PI * (3 - Math.sqrt(5)), y = i * off - 1 + off / 2;
    var r = Math.sqrt(Math.max(0, 1 - y * y)), phi = i * inc;
    return new this.three.Vector3(Math.cos(phi) * r * R, y * R, Math.sin(phi) * r * R);
  },
  /* 三种布局（沿用评估原型的架构）：球体星云 / 流式纵列 / 立方晶格 */
  layoutPos: function (i, n) {
    var V = this.three.Vector3;
    if (this.mode === 'flow') {
      var t = n <= 1 ? 0.5 : i / (n - 1);
      return new V((Math.random() - .5) * 560, (t - .5) * 760, (Math.random() - .5) * 560);
    }
    if (this.mode === 'grid') {
      var side = Math.max(2, Math.ceil(Math.cbrt(n))), sp = 380;
      return new V(((i % side) / (side - 1) - .5) * sp * 2,
        ((Math.floor(i / side) % side) / (side - 1) - .5) * sp * 2,
        ((Math.floor(i / (side * side)) / Math.max(1, side - 1)) - .5) * sp * 2);
    }
    return this.fib(i, n, 340);
  },

  /* ── 连线：一条 LineSegments（顶点色区分引用/血缘/重复），另加血缘边的流动光点 ── */
  buildEdges: function () {
    var T = this.three, self = this;
    var ls = this.links;
    var pos = new Float32Array(ls.length * 6), col = new Float32Array(ls.length * 6);
    var cRel = new T.Color(0x7fa8cc), cBlood = new T.Color(0xa98cf0), cDup = new T.Color(0xe0b23c);
    ls.forEach(function (L, i) {
      var c = L.kind === 'blood' ? cBlood : L.kind === 'dup' ? cDup : cRel;
      var o = i * 6;
      var a = L.a.mesh.position, b = L.b.mesh.position;
      pos[o] = a.x; pos[o + 1] = a.y; pos[o + 2] = a.z;
      pos[o + 3] = b.x; pos[o + 4] = b.y; pos[o + 5] = b.z;
      col[o] = c.r; col[o + 1] = c.g; col[o + 2] = c.b;
      col[o + 3] = c.r; col[o + 4] = c.g; col[o + 5] = c.b;
    });
    var g = new T.BufferGeometry();
    g.setAttribute('position', new T.BufferAttribute(pos, 3));
    g.setAttribute('color', new T.BufferAttribute(col, 3));
    this.edgeLines = new T.LineSegments(g, new T.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: .38, blending: T.AdditiveBlending, depthWrite: false,
    }));
    this.group.add(this.edgeLines);
    // 血缘边光点（每条 3 颗，沿连线流动）
    var blood = ls.filter(function (L) { return L.kind === 'blood' && L.src && L.dst; });
    if (blood.length) {
      var fp = new Float32Array(blood.length * 3 * 3);
      var fg = new T.BufferGeometry();
      fg.setAttribute('position', new T.BufferAttribute(fp, 3));
      this.flow = new T.Points(fg, new T.PointsMaterial({
        color: 0xd8c6ff, size: 9, sizeAttenuation: true, map: this._dotHard,
        transparent: true, opacity: .95, blending: T.AdditiveBlending, depthWrite: false,
      }));
      this.flow.userData = { blood: blood, n: 3 };
      this.group.add(this.flow);
    }
  },

  /* ── 主循环（时间驱动：内置浏览器可达 180fps，按帧计量会快得离谱） ── */
  start: function () {
    if (this.active || !this.renderer) return;
    this.active = true;
    var self = this;
    this._lastT = 0;
    function frame(t) {
      if (!self.active) return;
      self.step(t);
      self.raf = requestAnimationFrame(frame);
    }
    this.raf = requestAnimationFrame(frame);
  },
  stop: function () {
    this.active = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  },
  /* 无循环渲染一帧（low 档 / 降级后的静态呈现，交互时按需重绘） */
  renderOnce: function () {
    if (!this.renderer) return;
    this.step(performance.now ? performance.now() : Date.now(), true);
  },

  step: function (t, once) {
    var now = t || (performance.now ? performance.now() : Date.now());
    var dt = this._lastT ? Math.min(0.05, (now - this._lastT) / 1000) : 0.016;
    this._lastT = now;
    var time = now * 0.001;
    var low = !Anim.on();
    if (this.rotate && !low && !this._drag && now > this._paused) this.group.rotation.y += 0.075 * dt;
    // 入场：错峰点亮
    var ent = this.entrance, k = 1;
    if (ent) {
      var p = Math.min(1, (now - ent.t0) / 1100);
      if (p >= 1) this.entrance = null;
    }
    var self = this;
    this.nodes.forEach(function (nd, i) {
      var m = nd.mesh;
      if (ent) {
        var d = Math.min(1, Math.max(0, ((now - ent.t0) - (i % 24) * 34) / 620));
        k = Anim.easeOutBack(d);
      }
      var br = 1 + 0.035 * Math.sin(time * 1.5 + m.userData.jitter);   // 呼吸（维持"活着"）
      var hs = (self.hover === nd.id || self.sel === nd.id) ? 1.3 : (self.hood && self.hood.has(nd.id)) ? 1.1 : 1;
      m.scale.setScalar(k * br * hs);
      if (nd.glow) {
        nd.glow.position.copy(m.position);
        var go = (self.hover === nd.id || self.sel === nd.id) ? .82 : .34;
        nd.glow.material.opacity = go * (Anim.degraded ? .5 : 1) * k;
        nd.glow.scale.setScalar(nd.r * (self.hover === nd.id ? 11 : 7) * k);
      }
    });
    if (this.flow && this.flow.userData.blood) {
      var bl = this.flow.userData.blood, npt = this.flow.userData.n;
      var arr = this.flow.geometry.attributes.position.array;
      bl.forEach(function (L, bi) {
        for (var j = 0; j < npt; j++) {
          var p2 = ((time * 0.42 + j / npt + bi * 0.11) % 1);
          var o = (bi * npt + j) * 3;
          arr[o] = L.src.mesh.position.x + (L.dst.mesh.position.x - L.src.mesh.position.x) * p2;
          arr[o + 1] = L.src.mesh.position.y + (L.dst.mesh.position.y - L.src.mesh.position.y) * p2;
          arr[o + 2] = L.src.mesh.position.z + (L.dst.mesh.position.z - L.src.mesh.position.z) * p2;
        }
      });
      this.flow.geometry.attributes.position.needsUpdate = true;
    }
    if (this.dust && !low) this.dust.rotation.y += 0.006 * dt;
    this.renderer.render(this.scene, this.camera);
    this.updateLabels(false);
    if (!once) Anim.reportFrame(now);
  },

  /* ── 悬浮标签（HTML 叠层）：枢纽 + 邻域，投影到屏幕；画面里最多十几枚，保持简约 ── */
  ensureLabels: function () {
    if (!this._labelBox) {
      var box = document.createElement('div');
      box.className = 'gl3d-labels';
      this.host.appendChild(box);
      this._labelBox = box;
    }
  },
  updateLabels: function (force) {
    if (!this._labelBox) return;
    var now = performance.now ? performance.now() : Date.now();
    if (!force && now - (this._lt || 0) < 90) return;
    this._lt = now;
    var T = this.three, cam = this.camera, W = this.W, H = this.H;
    var want = [];
    var seen = {};
    var add = function (nd) { if (nd && !seen[nd.id]) { seen[nd.id] = 1; want.push(nd); } };
    if (this.hover || this.sel) {
      var cur = this.byId[this.hover || this.sel];
      add(cur);
      if (this.hood) this.hood.forEach(function (id) { add(this.byId[id]); }, this);
    } else {
      // 空闲：只标枢纽（入度≥3），按入度取前 14 枚
      var hubs = this.nodes.filter(function (nd) { return nd.skill && nd.skill.indeg >= 3; })
        .sort(function (a, b) { return b.skill.indeg - a.skill.indeg; }).slice(0, 14);
      hubs.forEach(add);
    }
    var v = new T.Vector3();
    for (var i = 0; i < want.length; i++) {
      var nd = want[i];
      var el = this._labels[i];
      if (!el) {
        el = document.createElement('span');
        el.className = 'gl3d-label';
        this._labelBox.appendChild(el);
        this._labels[i] = el;
      }
      nd.mesh.getWorldPosition(v);
      var z = v.z;
      v.project(cam);
      var vis = v.z < 1 && Math.abs(v.x) < 1.06 && Math.abs(v.y) < 1.06;
      el.textContent = nd.id.slice(0, 20);
      el.style.transform = 'translate(-50%,-50%) translate(' + ((v.x * .5 + .5) * W).toFixed(0) + 'px,' +
        ((-v.y * .5 + .5) * H - (nd.r + 14) * (1 + 200 / Math.max(200, 900 + z))).toFixed(0) + 'px)';
      el.style.opacity = vis ? (this.hover === nd.id || this.sel === nd.id ? 1 : .78) : 0;
      el.className = 'gl3d-label' + (this.hover === nd.id || this.sel === nd.id ? ' on' : '');
    }
    for (var j = want.length; j < this._labels.length; j++) this._labels[j].style.opacity = 0;
  },

  /* ── 交互：拖拽旋转 / 滚轮推拉 / 射线拾取悬停与点选 ── */
  bind: function (onPick, onHover, onLeave) {
    this._cb = { pick: onPick, hover: onHover, leave: onLeave };
    var self = this, el = this.canvas;
    if (this._upHandler) { window.removeEventListener('pointerup', this._upHandler); this._upHandler = null; }
    var down = false;
    el.addEventListener('pointerdown', function (e) {
      down = true; self._drag = null;
      self._down = { x: e.clientX, y: e.clientY, rx: self.group.rotation.x, ry: self.group.rotation.y, moved: false };
      try { el.setPointerCapture(e.pointerId); } catch (err) { }
    });
    el.addEventListener('pointermove', function (e) {
      if (down && self._down) {
        var dx = e.clientX - self._down.x, dy = e.clientY - self._down.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) self._down.moved = true;
        if (self._down.moved) {
          self.group.rotation.y = self._down.ry + dx * 0.006;
          self.group.rotation.x = Math.max(-1.35, Math.min(1.35, self._down.rx + dy * 0.006));
          self._paused = (performance.now ? performance.now() : Date.now()) + 2600;
          if (!Anim.on()) self.renderOnce();
          return;
        }
      }
      self.pick(e, self._cb.hover);
    });
    var up = function (e) {
      if (down && self._down && !self._down.moved && self._cb.pick) self.pick(e, null, self._cb.pick);
      down = false; self._down = null;
    };
    this._upHandler = up;
    window.addEventListener('pointerup', up);
    el.addEventListener('pointerleave', function () { if (self._cb.leave) self._cb.leave(); });
    el.addEventListener('wheel', function (e) {
      e.preventDefault();
      var z = self.camera.position.z + e.deltaY * 0.7;
      self.camera.position.z = Math.max(380, Math.min(2200, z));
      self._paused = (performance.now ? performance.now() : Date.now()) + 2600;
      if (!Anim.on()) self.renderOnce();
    }, { passive: false });
  },
  _ray: null,
  pick: function (e, onHover, onClick) {
    var T = this.three;
    if (!this._ray) {
      this._ray = new T.Raycaster();
      this._ndc = new T.Vector2();
    }
    var r = this.canvas.getBoundingClientRect();
    this._ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    this._ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    this._ray.setFromCamera(this._ndc, this.camera);
    var hits = this._ray.intersectObjects(this.nodes.filter(function (n) { return n.mesh; }).map(function (n) { return n.mesh; }), false);
    var nd = hits.length ? hits[0].object.userData.nd : null;
    if (onHover) { this.hover = nd ? nd.id : null; onHover(nd, e); }
    else if (onClick && nd) onClick(nd, e);
    else if (onClick && !nd) onClick(null, e);
    if (!Anim.on()) this.renderOnce();
    return nd;
  },

  /* 换色板：更新玻璃体色（材质色 = 类别色；衰减色同步，否则体色不一致） */
  recolor: function (catColors) {
    if (!this.ready || !this.nodes.length) return;
    var T = this.three;
    this.nodes.forEach(function (nd) {
      if (nd.ci < 0 || !catColors[nd.ci]) return;
      var c = new T.Color(catColors[nd.ci].base);
      nd.mesh.material.color.copy(c);
      nd.mesh.material.attenuationColor.copy(c);
      nd.mesh.userData.base.copy(c);
      if (nd.glow) nd.glow.material.color.copy(c);
    });
  },
  /* 降级：星尘减半 + 泛光减半 + 停自转 */
  degrade: function () {
    if (!this.ready) return;
    this.rotate = false;
    this.glows.forEach(function (g) { g.s.material.opacity *= 0.5; });
    if (this.dust) this.dust.material.size = 2.4;
  },
  setRotate: function (on) { this.rotate = !!on; if (!Anim.on()) this.renderOnce(); },
};
