  window._initRanGlobe = function() {
    if (document.getElementById('ranGlobeCanvas')._ranDone) return;
    document.getElementById('ranGlobeCanvas')._ranDone = true;
    try {
        var canvas = document.getElementById('ranGlobeCanvas');
        var renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setSize(window.innerWidth, window.innerHeight - 72);
        var scene = new THREE.Scene();
        var camera = new THREE.PerspectiveCamera(48, window.innerWidth / (window.innerHeight - 72), 0.1, 500);
        camera.position.z = 5;
        var sv = new Float32Array(7000 * 3);
        for (var i = 0; i < sv.length; i++) sv[i] = (Math.random() - .5) * 300;
        var sGeo = new THREE.BufferGeometry();
        sGeo.setAttribute('position', new THREE.BufferAttribute(sv, 3));
        scene.add(new THREE.Points(sGeo, new THREE.PointsMaterial({ color: 0xffffff, size: 0.07, transparent: true, opacity: .5 })));
        var G = new THREE.Group(); G.position.x = 2.4; scene.add(G);
        var R = 2.0;
        G.add(new THREE.Mesh(new THREE.SphereGeometry(R, 64, 64), new THREE.MeshPhongMaterial({ color: 0x020912, emissive: 0x05101e, specular: 0x334455, shininess: 12 })));
        G.add(new THREE.Mesh(new THREE.SphereGeometry(R + .008, 32, 32), new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: .05 })));
        function addRing(lat, op) {
          var phi = (90 - lat) * Math.PI / 180, ry = (R + .014) * Math.cos(phi), rr = (R + .014) * Math.sin(phi), pts = [];
          for (var i = 0; i <= 160; i++) { var t = (i / 160) * Math.PI * 2; pts.push(new THREE.Vector3(Math.cos(t) * rr, ry, Math.sin(t) * rr)); }
          G.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: op })));
        }
        addRing(0,.28); addRing(30,.13); addRing(-30,.13); addRing(60,.07); addRing(-60,.07);
        var cities = [[3.14,101.68],[1.35,103.82],[13.75,100.52],[21.03,105.85],[10.82,106.63],[14.55,121.0],[-6.21,106.85],[22.3,114.18],[31.23,121.47],[35.68,139.69],[28.61,77.23],[19.08,72.88],[51.51,-0.12],[48.85,2.35],[40.71,-74.0],[37.77,-122.4],[-33.87,151.21],[55.75,37.62],[30.05,31.25],[-23.55,-46.63],[25.2,55.27],[24.69,46.72],[41.01,28.97]];
        var dGeo = new THREE.SphereGeometry(.019,8,8), dMat = new THREE.MeshBasicMaterial({color:0xffffff}), dgGeo = new THREE.SphereGeometry(.036,8,8), dgMat = new THREE.MeshBasicMaterial({color:0xffffff,transparent:true,opacity:.16});
        cities.forEach(function(c) {
          var phi=(90-c[0])*Math.PI/180, theta=(c[1]+180)*Math.PI/180;
          var pos=new THREE.Vector3(-(R+.045)*Math.sin(phi)*Math.cos(theta),(R+.045)*Math.cos(phi),(R+.045)*Math.sin(phi)*Math.sin(theta));
          var d=new THREE.Mesh(dGeo,dMat); d.position.copy(pos); G.add(d);
          var g=new THREE.Mesh(dgGeo,dgMat); g.position.copy(pos); G.add(g);
        });
        scene.add(new THREE.AmbientLight(0x1a2233, 2.5));
        var dl=new THREE.DirectionalLight(0xffffff,.7); dl.position.set(5,3,4); scene.add(dl);
        (function tick(){ requestAnimationFrame(tick); G.rotation.y += 0.0018; renderer.render(scene, camera); })();
        window.addEventListener('resize', function(){ camera.aspect=window.innerWidth/(window.innerHeight-72); camera.updateProjectionMatrix(); renderer.setSize(window.innerWidth, window.innerHeight-72); });
      } catch(e) { console.warn('RAN globe failed:', e); }
  };

  function toggleRanAbout() {
    var c = document.getElementById('ranAboutContent');
    var ch = document.getElementById('ranAboutChevron');
    var open = c.style.display !== 'none';
    c.style.display = open ? 'none' : 'block';
    ch.style.transform = open ? '' : 'rotate(180deg)';
  }

  function switchRanTab(tab) {
    var tabs = ['sector','forecast','congested','plot'];
    tabs.forEach(function(t) {
      var panel = document.getElementById('ranPanel' + t.charAt(0).toUpperCase() + t.slice(1));
      var btn   = document.getElementById('ranTabBtn'  + t.charAt(0).toUpperCase() + t.slice(1));
      if (t === tab) {
        panel.classList.remove('hidden');
        btn.style.background    = 'rgba(255,255,255,0.06)';
        btn.style.borderBottom  = '2px solid #e8f0f8';
        btn.style.color         = '#e8f0f8';
      } else {
        panel.classList.add('hidden');
        btn.style.background    = 'none';
        btn.style.borderBottom  = '2px solid transparent';
        btn.style.color         = '#3a5c75';
      }
    });
  }
