      (function(){
        function initSidebarGlobe(){
          try {
            var canvas = document.getElementById('sidebarGlobeCanvas');
            if (!canvas || canvas._done) return; canvas._done = true;
            var W = 350, H = canvas.parentElement.offsetHeight || 600;
            canvas.width = W; canvas.height = H;
            var renderer = new THREE.WebGLRenderer({canvas:canvas, alpha:true, antialias:true});
            renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));
            renderer.setSize(W, H);
            var scene = new THREE.Scene();
            var camera = new THREE.PerspectiveCamera(48, W/H, 0.1, 500);
            camera.position.z = 4.2;
            var G = new THREE.Group(); G.position.set(0.6, -0.4, 0); scene.add(G);
            var R = 2.2;
            G.add(new THREE.Mesh(new THREE.SphereGeometry(R,64,64), new THREE.MeshPhongMaterial({color:0x020912,emissive:0x05101e,specular:0x334455,shininess:12})));
            G.add(new THREE.Mesh(new THREE.SphereGeometry(R+.01,32,32), new THREE.MeshBasicMaterial({color:0xffffff,wireframe:true,transparent:true,opacity:.06})));
            function ring(lat,op){var phi=(90-lat)*Math.PI/180,ry=(R+.015)*Math.cos(phi),rr=(R+.015)*Math.sin(phi),pts=[];for(var i=0;i<=120;i++){var t=(i/120)*Math.PI*2;pts.push(new THREE.Vector3(Math.cos(t)*rr,ry,Math.sin(t)*rr));}G.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),new THREE.LineBasicMaterial({color:0xffffff,transparent:true,opacity:op})));}
            ring(0,.3);ring(30,.14);ring(-30,.14);ring(60,.07);ring(-60,.07);
            var cities=[[3.14,101.68],[1.35,103.82],[13.75,100.52],[21.03,105.85],[35.68,139.69],[51.51,-0.12],[40.71,-74.0],[28.61,77.23],[55.75,37.62]];
            var dm=new THREE.MeshBasicMaterial({color:0xffffff}),dg=new THREE.MeshBasicMaterial({color:0xffffff,transparent:true,opacity:.18});
            cities.forEach(function(c){var phi=(90-c[0])*Math.PI/180,theta=(c[1]+180)*Math.PI/180,pos=new THREE.Vector3(-(R+.05)*Math.sin(phi)*Math.cos(theta),(R+.05)*Math.cos(phi),(R+.05)*Math.sin(phi)*Math.sin(theta));var d=new THREE.Mesh(new THREE.SphereGeometry(.02,8,8),dm);d.position.copy(pos);G.add(d);var g=new THREE.Mesh(new THREE.SphereGeometry(.04,8,8),dg);g.position.copy(pos);G.add(g);});
            scene.add(new THREE.AmbientLight(0x1a2233,2.5));
            var dl=new THREE.DirectionalLight(0xffffff,.6);dl.position.set(4,3,3);scene.add(dl);
            (function tick(){requestAnimationFrame(tick);G.rotation.y+=0.0016;renderer.render(scene,camera);})();
          } catch(e){ console.warn('Sidebar globe error:',e); }
        }
        // Init after map tab is shown (sidebar is in mapView)
        var _tried = false;
        var orig = window.switchMainTab;
        window.switchMainTab = function(tab){
          if(orig) orig(tab);
          if(!_tried && tab==='map'){ _tried=true; setTimeout(initSidebarGlobe, 100); }
        };
        // Also try on load if map is default
        document.addEventListener('DOMContentLoaded', function(){
          setTimeout(initSidebarGlobe, 300);
        });
      })();
