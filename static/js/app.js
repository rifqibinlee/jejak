                async function checkSession() {
                    try {
                        const response = await fetch('/api/check_session');
                        if (!response.ok) {
                            // Session invalid, redirect to login or refresh
                            console.warn('Session invalid, redirecting...');
                            window.location.href = '/login?next=/map';
                            return false;
                        }
                        return true;
                    } catch (error) {
                        console.error('Session check failed:', error);
                        return false;
                    }
                }
        // --- 1. Map Initialization ---
        var map = L.map('map', {
            zoomControl: false,
            boxZoom: true,
            preferCanvas: true
        }).setView([4.2105, 101.9758], 6);
        L.control.zoom({ position: 'topright' }).addTo(map);

        var satelliteLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { attribution: 'Tiles &copy; Esri', maxZoom: 19 });
        var labelsLayer    = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png', { attribution: '&copy;OpenStreetMap, &copy;CartoDB', subdomains: 'abcd', maxZoom: 19 });
        var streetLayer    = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { attribution: '&copy;OpenStreetMap, &copy;CartoDB', subdomains: 'abcd', maxZoom: 19 });
        var demLayer       = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}', {
            attribution: 'Hillshade &copy; Esri, USGS, NOAA',
            maxZoom: 19,
            opacity: 0.55,
            zIndex: 3
        });

        satelliteLayer.addTo(map);
        labelsLayer.addTo(map);
        var isSatelliteMode = true;
        var isDemActive     = false;

        // --- Traffic Layer (TomTom) ---
        var trafficLayer = L.tileLayer(
            'https://{s}.api.tomtom.com/traffic/map/4/tile/flow/relative/{z}/{x}/{y}.png?key=DEMO_KEY&tileSize=256',
            {
                attribution: '&copy; <a href="https://www.tomtom.com" target="_blank">TomTom</a> Traffic',
                subdomains: ['a','b','c','d'],
                maxZoom: 19,
                opacity: 0.7,
                zIndex: 5
            }
        );
        var trafficIncidentLayer = L.tileLayer(
            'https://{s}.api.tomtom.com/traffic/map/4/tile/incidents/s3/{z}/{x}/{y}.png?key=DEMO_KEY&tileSize=256',
            {
                attribution: '&copy; <a href="https://www.tomtom.com" target="_blank">TomTom</a> Incidents',
                subdomains: ['a','b','c','d'],
                maxZoom: 19,
                opacity: 0.85,
                zIndex: 6
            }
        );
        var trafficEnabled = false;
        var layerMenuOpen = false;

        // Layer state tracking (mirrors what's actually on the map)
        var layerStates = {
            clusters:   true,   // added to map by default via map.addLayer(markers)
            congestion: false,
            heatmap:    false,
            '5g':       false,
            '4g':       false,
            '3g':       false,
            '2g':       false,
           sig100_120:   true,
           sig121_130:   true,
           sig131_worse: true,
           noise:      false
        };

        var layerMap = {}; // populated after layers are declared

        function initLayerMap() {
            layerMap = {
                clusters:   markers,
                congestion: congestedMarkers,
                heatmap:    heatmapLayer,
                '5g':       layer5G,
                '4g':       layer4G,
                '3g':       layer3G,
                '2g':       layer2G,
                sig100_120:   layerSig100_120,
                sig121_130:   layerSig121_130,
                sig131_worse: layerSig131_worse,
                noise:      layerNoise
            };
        }

        function toggleLayerMenu() {
            layerMenuOpen = !layerMenuOpen;
            const menu = document.getElementById('layerMenu');
            const chevron = document.getElementById('layerMenuChevron');
            if (layerMenuOpen) {
                menu.style.display = 'flex';
                chevron.style.transform = 'rotate(180deg)';
            } else {
                menu.style.display = 'none';
                chevron.style.transform = 'rotate(0deg)';
            }
        }

        // Close menu when clicking outside
        document.addEventListener('click', function(e) {
            const ctrl = document.getElementById('mapLayerControl');
            if (ctrl && !ctrl.contains(e.target) && layerMenuOpen) {
                layerMenuOpen = false;
                document.getElementById('layerMenu').style.display = 'none';
                document.getElementById('layerMenuChevron').style.transform = 'rotate(0deg)';
            }
        });

        function toggleTrafficLayer() {
            trafficEnabled = !trafficEnabled;
            const sw = document.getElementById('trafficToggleSwitch');
            if (trafficEnabled) {
                trafficLayer.addTo(map);
                trafficIncidentLayer.addTo(map);
                sw.classList.add('active');
            } else {
                map.removeLayer(trafficLayer);
                map.removeLayer(trafficIncidentLayer);
                sw.classList.remove('active');
            }
        }

        function toggleOverlayLayer(key) {
            if (!layerMap[key]) initLayerMap();
            const layer = layerMap[key];
            const sw = document.getElementById('toggle-' + key);
            if (!layer || !sw) return;

            layerStates[key] = !layerStates[key];
            if (layerStates[key]) {
                layer.addTo(map);
                sw.classList.add('active');
            } else {
                map.removeLayer(layer);
                sw.classList.remove('active');
            }
            if (typeof updateMapLegend === 'function') updateMapLegend();
        }

        var geoserverWmsEntries = [];

        async function loadGeoserverMapLayers() {
            var section = document.getElementById('geoserverSection');
            var rows = document.getElementById('geoserverLayerRows');
            if (!section || !rows || typeof map === 'undefined') return;
            try {
                var res = await fetch('/api/geoserver/config', { credentials: 'same-origin' });
                if (!res.ok) return;
                var cfg = await res.json();
                if (!cfg.enabled || !cfg.layers || !cfg.layers.length) return;
                section.style.display = 'block';
                rows.innerHTML = '';
                geoserverWmsEntries.forEach(function (e) {
                    if (e.layer && map.hasLayer(e.layer)) map.removeLayer(e.layer);
                });
                geoserverWmsEntries = [];
                var wmsBase = window.location.origin + (cfg.wmsPath || '/api/geoserver/wms');
                cfg.layers.forEach(function (ly, idx) {
                    var switchId = 'toggle-geoserver-' + idx;
                    var op = (ly.opacity != null && !isNaN(ly.opacity)) ? ly.opacity : 0.65;
                    var wms = L.tileLayer.wms(wmsBase, {
                        layers: ly.layers,
                        format: 'image/png',
                        transparent: true,
                        version: '1.1.1',
                        opacity: op,
                        zIndex: 400,
                    });
                    geoserverWmsEntries.push({ layer: wms, active: false });
                    var label = document.createElement('label');
                    label.className = 'layer-menu-item';
                    label.setAttribute('onclick', 'event.preventDefault(); toggleGeoserverLayer(' + idx + ', "' + switchId + '");');
                    label.innerHTML =
                        '<div class="layer-menu-icon" style="background:linear-gradient(135deg,#0e7490,#155e75);">' +
                        '<i class="fas fa-draw-polygon" style="color:white;font-size:0.65rem;"></i></div>' +
                        '<span class="layer-menu-label">' + (ly.title || ly.layers).replace(/</g, '&lt;') + '</span>' +
                        '<div class="layer-toggle-switch" id="' + switchId + '"><div class="layer-toggle-knob"></div></div>';
                    rows.appendChild(label);
                });
            } catch (e) {
                console.warn('GeoServer config unavailable', e);
            }
        }

        function toggleGeoserverLayer(idx, switchId) {
            var entry = geoserverWmsEntries[idx];
            if (!entry || !entry.layer) return;
            var sw = document.getElementById(switchId);
            entry.active = !entry.active;
            if (entry.active) {
                entry.layer.addTo(map);
                if (sw) sw.classList.add('active');
            } else {
                map.removeLayer(entry.layer);
                if (sw) sw.classList.remove('active');
            }
            if (typeof updateMapLegend === 'function') updateMapLegend();
        }

        // ── GeoServer WMS GetFeatureInfo on map click ──────────────────────
        var _geoInfoPopup = L.popup({ maxWidth: 340, className: 'geo-info-popup' });

        map.on('click', async function (e) {
            var activeEntries = geoserverWmsEntries.filter(function (en) { return en.active && en.layer; });
            if (!activeEntries.length) return;

            var size    = map.getSize();
            var bounds  = map.getBounds();
            var nw      = bounds.getNorthWest();
            var se      = bounds.getSouthEast();
            var bbox    = nw.lng + ',' + se.lat + ',' + se.lng + ',' + nw.lat;
            var px      = map.latLngToContainerPoint(e.latlng);

            var layerNames = activeEntries.map(function (en) {
                return en.layer.wmsParams && en.layer.wmsParams.layers;
            }).filter(Boolean).join(',');

            var wmsBase = window.location.origin + '/api/geoserver/wms';
            var url = wmsBase +
                '?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetFeatureInfo' +
                '&LAYERS='     + encodeURIComponent(layerNames) +
                '&QUERY_LAYERS='+ encodeURIComponent(layerNames) +
                '&BBOX='       + bbox +
                '&WIDTH='      + size.x +
                '&HEIGHT='     + size.y +
                '&X='          + Math.round(px.x) +
                '&Y='          + Math.round(px.y) +
                '&INFO_FORMAT=application/json' +
                '&FEATURE_COUNT=5' +
                '&SRS=EPSG:4326';

            try {
                var res  = await fetch(url, { credentials: 'same-origin' });
                if (!res.ok) return;
                var data = await res.json();
                var features = (data.features || []).filter(function (f) {
                    return f.properties && Object.keys(f.properties).length > 0;
                });
                if (!features.length) return;

                var rows = features.map(function (f, fi) {
                    var props = f.properties;
                    var layerName = (f.id || '').split('.')[0] || layerNames.split(',')[0];
                    var inner = Object.keys(props).filter(function (k) {
                        var v = props[k];
                        return v !== null && v !== '' && k.toLowerCase() !== 'geom' && k.toLowerCase() !== 'the_geom';
                    }).map(function (k) {
                        return '<tr><td style="color:#64748b;padding:2px 6px 2px 0;font-size:0.68rem;white-space:nowrap;">' +
                            k + '</td><td style="font-weight:600;font-size:0.7rem;padding:2px 0;">' +
                            String(props[k]).replace(/</g,'&lt;') + '</td></tr>';
                    }).join('');
                    return '<div style="' + (fi > 0 ? 'margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb;' : '') + '">' +
                        '<div style="font-size:0.65rem;font-weight:800;color:#0e7490;text-transform:uppercase;margin-bottom:4px;">' +
                        '<i class="fas fa-draw-polygon" style="margin-right:4px;"></i>' + layerName.replace(/_/g,' ') +
                        '</div><table style="width:100%;border-collapse:collapse;">' + inner + '</table></div>';
                }).join('');

                _geoInfoPopup
                    .setLatLng(e.latlng)
                    .setContent(
                        '<div style="font-family:Inter,sans-serif;max-height:260px;overflow-y:auto;">' +
                        rows + '</div>'
                    )
                    .openOn(map);
            } catch (_) {}
        });

        var siteDataCache = [];
        var activeCoverageLayers = [];
        var siteMarkerMap = {};
        var connectionLines = [];

        var markers = L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 60, iconCreateFunction: function(c) { return new L.DivIcon({ html: '<div class="cluster-base cluster-normal"><span>' + c.getChildCount() + '</span></div>', className: 'marker-cluster', iconSize: new L.Point(40, 40) }); } });
        var congestedMarkers = L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 60, iconCreateFunction: function(c) { return new L.DivIcon({ html: '<div class="cluster-base cluster-congested"><span>' + c.getChildCount() + '</span></div>', className: 'marker-cluster', iconSize: new L.Point(40, 40) }); } });
        var heatmapLayer = L.heatLayer([], { radius: 40, blur: 25, maxZoom: 15, minOpacity: 0.6, gradient: { 0.3: 'blue', 0.5: 'lime', 0.7: 'yellow', 0.85: 'orange', 0.95:'red' } });

        var layer5G = L.layerGroup(), layer4G = L.layerGroup(), layer3G = L.layerGroup(), layer2G = L.layerGroup();
        
        var layerSig100_120 = L.featureGroup().addTo(map);
        var layerSig121_130 = L.featureGroup().addTo(map);
        var layerSig131_worse = L.featureGroup().addTo(map);
        var layerNoise = L.layerGroup();
        
        // ADD THESE TWO LINES HERE
        var layerMRHoles = L.featureGroup().addTo(map);
        var layerOoklaHoles = L.featureGroup().addTo(map);

        map.addLayer(markers);

        var overlayMaps = {
            "<span class='font-bold text-blue-600'>Site Clusters</span>": markers,
            "<span class='font-bold text-red-600'>Congestion Counts</span>": congestedMarkers,
            "<span class='font-bold text-orange-500'>Heatmap</span>": heatmapLayer,
            "<span class='font-bold text-yellow-500'>5G Coverage</span>": layer5G,
            "<span class='font-bold text-blue-400'>4G Coverage</span>": layer4G,
            "<span class='font-bold text-orange-400'>3G Coverage</span>": layer3G,
            "<span class='font-bold text-gray-500'>2G Coverage</span>": layer2G,
            "<span class='font-bold text-blue-800'><i class='fas fa-square mr-1'></i>MR Coverage Holes (Square)</span>": layerMRHoles,
            "<span class='font-bold text-purple-700'><i class='fas fa-caret-up mr-1'></i>Ookla Coverage Holes (Triangle)</span>": layerOoklaHoles,
            "<span class='font-bold text-black'>Signal Noise (-1)</span>": layerNoise
        };
        // Native layer control replaced by custom Google Maps-style menu
        initLayerMap();

        var currentModalSiteId = null;

        async function init() {
            await loadWeeks();
            await loadRegions();
            await loadMapData();
            loadLeaderboard();
            loadWorstClusters();
            setupMapAutocomplete();
            loadCoverageHoles();
            loadAnnotations();
            loadNotes();
            loadTasks();
            loadGeoserverMapLayers();
            if (typeof updateMapLegend === 'function') updateMapLegend();
        }

        async function loadRegions() {
            const res = await fetch('/api/filters/regions');
            const regions = await res.json();
            const select = document.getElementById('regionSelect');
            regions.forEach(r => {
                const opt = document.createElement('option');
                opt.value = r; opt.textContent = r;
                select.appendChild(opt);
            });
        }

        function updateFilters() {
            loadMapData();
            loadLeaderboard();
            loadCoverageHoles();
        }

        async function loadWeeks() {
            const res = await fetch('/api/weeks');
            const weeks = await res.json();
            if(weeks.length > 0) {
                // --- CHANGED: Map the JSON objects into formatted dropdown strings ---
                document.getElementById('weekSelect').innerHTML = weeks.map(w => 
                    `<option value="${w.year}-${w.week}">Week ${w.week} (${w.year})</option>`
                ).join('');
            }
        }

        async function loadLeaderboard() {
            // --- CHANGED: Extract year and week safely from the new format ---
            const rawWeek = document.getElementById('weekSelect').value;
            let week = rawWeek;
            let year = document.getElementById('globalFilterYear')?.value || "2026";

            if (rawWeek && rawWeek.includes('-')) {
                const parts = rawWeek.split('-');
                year = parts[0];
                week = parts[1];
            }

            const region = document.getElementById('regionSelect').value;
            const res = await fetch(`/api/map/top_congested?week=${week}&region=${region}&year=${year}`);
            const data = await res.json();

            document.getElementById('leaderboardList').innerHTML = data.map((d, i) => `
                <div class="leaderboard-item" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:7px 8px;border-bottom:1px solid rgba(255,255,255,0.06);background:transparent;transition:background .15s;" onmouseover="this.style.background='rgba(248,113,113,0.07)'" onmouseout="this.style.background='transparent'" onclick="zoomToSite('${d.zoom_sector_id.split('_')[0]}')">
                    <span style="font-weight:700;color:#e8f0f8;font-size:11px;font-family:'DM Sans',sans-serif;">#${i+1} ${d.zoom_sector_id}</span>
                    <span style="background:rgba(248,113,113,0.15);color:#f87171;padding:2px 7px;font-size:10px;font-weight:700;font-family:'Raleway',sans-serif;letter-spacing:.5px;white-space:nowrap;">${d.congested_weeks} Wks</span>
                </div>
            `).join('');
        }

        async function loadWorstClusters() {
            try {
                const res = await fetch('/api/map/worst_clusters');
                const data = await res.json();

                document.getElementById('mrClusterList').innerHTML = data.mr.map((c, i) => `
                    <div class="leaderboard-item" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:7px 8px;border-bottom:1px solid rgba(255,255,255,0.06);background:transparent;transition:background .15s;" onmouseover="this.style.background='rgba(96,165,250,0.07)'" onmouseout="this.style.background='transparent'"
                          onclick="focusCluster(${c.cluster_id}, 'MR', ${c.center_lat}, ${c.center_lon})">
                        <span style="font-weight:700;color:#e8f0f8;font-size:11px;font-family:'DM Sans',sans-serif;">#${i+1} Cluster ${c.cluster_id} (${c.point_count} pts)</span>
                        <span style="background:rgba(248,113,113,0.15);color:#f87171;padding:2px 7px;font-size:10px;font-weight:700;font-family:'Raleway',sans-serif;letter-spacing:.5px;white-space:nowrap;">${Number(c.avg_signal).toFixed(1)} dBm</span>
                    </div>
                `).join('') || '<p style="font-size:11px;color:#3a5c75;padding:8px;">No MR data found.</p>';

                document.getElementById('ooklaClusterList').innerHTML = data.ookla.map((c, i) => `
                    <div class="leaderboard-item" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:7px 8px;border-bottom:1px solid rgba(255,255,255,0.06);background:transparent;transition:background .15s;" onmouseover="this.style.background='rgba(167,139,250,0.07)'" onmouseout="this.style.background='transparent'"
                          onclick="focusCluster(${c.cluster_id}, 'Ookla', ${c.center_lat}, ${c.center_lon})">
                        <span style="font-weight:700;color:#e8f0f8;font-size:11px;font-family:'DM Sans',sans-serif;">#${i+1} Cluster ${c.cluster_id} (${c.point_count} pts)</span>
                        <span style="background:rgba(248,113,113,0.15);color:#f87171;padding:2px 7px;font-size:10px;font-weight:700;font-family:'Raleway',sans-serif;letter-spacing:.5px;white-space:nowrap;">${Number(c.avg_signal).toFixed(1)} dBm</span>
                    </div>
                `).join('') || '<p style="font-size:11px;color:#3a5c75;padding:8px;">No Ookla data found.</p>';

            } catch (e) { console.error("Error loading clusters", e); }
        }

        function focusCluster(clusterId, dataSource, lat, lng) {
            map.flyTo([lat, lng], 15, { animate: true, duration: 1.5 });
            if (connectionLines) { connectionLines.forEach(l => map.removeLayer(l)); connectionLines = []; }
            const targetLayer = (dataSource === 'MR') ? layerMRHoles : layerOoklaHoles;

            targetLayer.eachLayer(function(marker) {
                if (marker.options.clusterId === clusterId) {
                    var servCell = marker.options.servingCell || "Unknown";
                    var siteId = servCell.split('_')[0].split('-')[0].toUpperCase();
                    var servingSite = siteDataCache.find(s => s.site_id.toUpperCase() === siteId);

                    if (servingSite) {
                        var siteLatLng = L.latLng(servingSite.lat, servingSite.lng);
                        var holeLatLng = marker.getLatLng();
                        var line = L.polyline([holeLatLng, siteLatLng], {
                            color: '#dc2626', weight: 1.5, dashArray: '5, 5', opacity: 0.8
                        }).addTo(map);
                        var distMeters = map.distance(holeLatLng, siteLatLng);
                        var distDisplay = distMeters > 1000 ? (distMeters/1000).toFixed(2) + ' km' : Math.round(distMeters) + ' m';
                        line.bindTooltip(`${siteId}: ${distDisplay}`, { permanent: false, sticky: true });
                        connectionLines.push(line);
                    }
                }
            });
        }

        async function loadMapData(weekOverride = null) {
            document.getElementById('loading').classList.remove('hidden');
            const params = new URLSearchParams();

            // --- CHANGED: Extract year and week safely from the new format ---
            const rawWeek = weekOverride || document.getElementById('weekSelect').value;
            let currentWeek = rawWeek;
            let currentYear = document.getElementById('globalFilterYear')?.value;

            if (rawWeek && rawWeek.includes('-')) {
                const parts = rawWeek.split('-');
                currentYear = parts[0];
                currentWeek = parts[1];
            }
            
            if (!currentYear || currentYear === 'All') currentYear = "2026"; 
            
            params.set('year', currentYear);
            if (currentWeek && currentWeek !== 'All') params.set('week', currentWeek);

            const currentRegion = document.getElementById('regionSelect').value;
            if (currentRegion && currentRegion !== 'All') params.set('region', currentRegion);

            let url = `/api/sites?${params.toString()}`;

            try {
                const response = await fetch(url);
                const data = await response.json();

                if (data.error) {
                    console.error("Server Error:", data.error);
                    alert("Database Error: " + data.error);
                    return;
                }

                // 1. FILTER BAD DATA IMMEDIATELY
                const validSites = data.filter(s => {
                    if (!s || !s.site_id) return false;
                    const lat = parseFloat(s.lat);
                    const lng = parseFloat(s.lng);
                    return !isNaN(lat) && !isNaN(lng) && (lat !== 0 || lng !== 0);
                });

                // 2. CACHE ONLY THE CLEAN DATA
                siteDataCache = validSites;

                markers.clearLayers();
                congestedMarkers.clearLayers();
                heatmapLayer.setLatLngs([]);
                clearCoverage();
                siteMarkerMap = {};

                // Visible site count is now driven by viewport; trigger after markers load
                setTimeout(updateVisibleSiteCount, 100);

                const mList = []; const cList = []; const heatPoints = []; let congCount = 0;

                // 3. LOOP SAFELY
                validSites.forEach(site => {
                    site.lat = parseFloat(site.lat);
                    site.lng = parseFloat(site.lng);

                    drawSiteCoverage(site);

                    let isCongested = false;

                    if(site.sectors) {
                        const areaStr = String(site.area_target || '').toLowerCase();
                        const isUrban = areaStr.includes('urban') || areaStr.includes('kmc');
                        const prbThreshold = isUrban ? 80.0 : 92.0;

                        for(let sec of site.sectors) {
                            const p = parseFloat(sec.prb ?? 0);
                            if(p >= prbThreshold) {
                                isCongested = true;
                                break;
                            }
                        }
                    }

                    if (isCongested) { heatPoints.push([site.lat, site.lng, 1.0]); congCount++; }
                    
                    const markerColor = isCongested ? '#dc2626' : '#2563eb';
                    
                    // --- NEW: Added floating Site ID label above the dot ---
                    const customIcon = L.divIcon({ 
                        className: 'custom-pin', 
                        html: `
                            <div style="position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%); font-size: 10px; font-weight: 900; color: #1e293b; text-shadow: 2px 2px 0 #fff, -2px -2px 0 #fff, 2px -2px 0 #fff, -2px 2px 0 #fff, 0 2px 0 #fff, 0 -2px 0 #fff, 2px 0 0 #fff, -2px 0 0 #fff; white-space: nowrap; pointer-events: none; letter-spacing: 0.5px;">
                                ${site.site_id}
                            </div>
                            <div style="background-color:${markerColor}; width:14px; height:14px; border-radius:50%; border:2px solid white; box-shadow:0 2px 5px rgba(0,0,0,0.3);"></div>
                        `, 
                        iconSize: [14, 14],
                        iconAnchor: [7, 7] // Keeps the dot perfectly centered on the coordinate
                    });

                    const popupContent = createPopupContent(site, isCongested);
                    var marker = L.marker([site.lat, site.lng], {icon: customIcon}).bindPopup(popupContent);

                    marker.on('click', (e) => { if (e.originalEvent.ctrlKey) { e.originalEvent.stopPropagation(); drawSiteCoverage(site, true); }});
                    mList.push(marker); siteMarkerMap[site.site_id.toUpperCase()] = marker;
                    if(isCongested) cList.push(L.marker([site.lat, site.lng], {icon: customIcon}).bindPopup(popupContent));
                });

                markers.addLayers(mList); congestedMarkers.addLayers(cList);
                if (heatPoints.length > 0) heatmapLayer.setLatLngs(heatPoints);
                setTimeout(updateVisibleSiteCount, 100);

            } catch (e) {
                console.error("Map Data Load Error:", e);
            } finally {
                document.getElementById('loading').classList.add('hidden');
            }
        }

        // =========================================================
        // VIEW TOGGLE LOGIC (KPI -> MATRIX -> UPGRADE -> KPI)
        // =========================================================
        window.togglePopupView = function(popupId, siteId) {
            const kpi = document.getElementById('kpi_' + popupId);
            const mat = document.getElementById('mat_' + popupId);
            const upg = document.getElementById('upg_' + popupId);
            const btn = document.getElementById('btn_' + popupId);
            
            // --- FIX: Safely split the YYYY-WW format ---
            const rawWeek = document.getElementById('weekSelect').value;
            let week = rawWeek;
            let year = document.getElementById('globalFilterYear') ? document.getElementById('globalFilterYear').value : new Date().getFullYear();

            if (rawWeek && rawWeek.includes('-')) {
                const parts = rawWeek.split('-');
                year = parts[0];
                week = parts[1];
            }

            // STATE 1: KPI -> Show MATRIX
            if (!kpi.classList.contains('hidden')) {
                kpi.classList.add('hidden');
                mat.classList.remove('hidden');
                upg.classList.add('hidden');
                btn.innerHTML = 'CONFIG & UPGRADE <i class="fas fa-arrow-right"></i>';
            }
            // STATE 2: MATRIX -> Show UPGRADE
            else if (!mat.classList.contains('hidden')) {
                mat.classList.add('hidden');
                upg.classList.remove('hidden');
                kpi.classList.add('hidden');
                btn.innerHTML = 'BACK TO KPI <i class="fas fa-undo"></i>';

                // Fetch Data on Demand (Passing BOTH week and year)
                loadUpgradeData(popupId, siteId, week, year);
            }
            // STATE 3: UPGRADE -> Show KPI
            else {
                upg.classList.add('hidden');
                kpi.classList.remove('hidden');
                mat.classList.add('hidden');
                btn.innerHTML = 'NEXT <i class="fas fa-arrow-right"></i>';
            }
        };

        // =========================================================
        // LOAD UPGRADE DATA (MATRIX FORMAT + HIGHLIGHTS)
        // =========================================================
        function loadUpgradeData(popupId, siteId, week, year) {
            const contentDiv = document.getElementById('upg_content_' + popupId);
            if (!contentDiv.innerHTML.includes('Loading')) return;

           // FIX: Added &year= to the fetch URL
           fetch(`/api/map/site_upgrade_details?site_id=${siteId}&week=${week}&year=${year}`)
                .then(r => r.json())
                .then(data => {
                    // PREVENTS THE UI FROM DRAWING BLANK DASHES IF THE API FAILS
                    if (data.error || !data.sectors) {
                        contentDiv.innerHTML = `<div class="text-red-500 p-4 font-bold text-center text-sm"><i class="fas fa-exclamation-triangle"></i> Backend Error: ${data.error || 'Failed to load Upgrade Data'}</div>`;
                        return;
                    }

                    const bands = ['L9', 'L18', 'L21', 'L26'];

                    // Display Area Target
                    let targetBadge = '';
                    const areaStr = String(data.area_target || '').toLowerCase();

                    if(areaStr.includes('urban') || areaStr.includes('kmc')) {
                        targetBadge = '<span class="badge badge-urban">Urban/KMC (Div: 0.8)</span>';
                    } else {
                        targetBadge = '<span class="badge badge-outside">Outside (Div: 0.92)</span>';
                    }

                    // --- NEW LOGIC: EXTRACT THE HIGHEST CASE LABEL ---
                    let highestCase = "";
                    let highestCaseNum = 0;

                    Object.values(data.sectors).forEach(sec => {
                        if(sec.case_label && sec.case_label.includes('Case')) {
                            let match = sec.case_label.match(/Case (\d+)/);
                            if(match) {
                                let num = parseInt(match[1]);
                                if(num > highestCaseNum) {
                                    highestCaseNum = num;
                                    highestCase = sec.case_label;
                                }
                            }
                        }
                    });

                    let caseBadge = '';
                    if (highestCaseNum > 0) {
                        caseBadge = `<span class="bg-white text-red-600 px-3 py-1 rounded-full font-bold text-[11px] shadow-sm ml-3 border border-red-200">
                                        <i class="fas fa-exclamation-circle mr-1"></i> ${highestCase}
                                     </span>`;
                    }

                    let html = `<div class="flex justify-between items-center mb-2 text-[10px] font-bold text-gray-600 bg-gray-50 p-2 rounded border border-gray-200">
                                    <div class="flex items-center">
                                        <span>Target: ≤ 73% Capacity</span>
                                        ${caseBadge}
                                        <button onclick="openSectorPricingModal('${escape(JSON.stringify(data.sectors))}')" class="bg-green-100 text-green-700 px-3 py-1 rounded text-[10px] font-bold border border-green-300 ml-3 hover:bg-green-200 shadow-sm transition">
                                            <i class="fas fa-file-invoice-dollar mr-1"></i> View Pricing Details
                                        </button>
                                    </div>
                                    ${targetBadge}
                                </div>`;

                    const buildMatrix = (title, type) => {
                        let tbl = `
                        <div class="mb-2 font-bold text-sm text-gray-700 mt-3 flex items-center gap-2">
                            ${type === 'upgrade' ? '<i class="fas fa-arrow-up text-blue-600"></i>' : '<i class="fas fa-history text-gray-500"></i>'}
                            ${title}
                        </div>
                        <div class="sector-table-container mb-2 overflow-hidden">
                            <table class="matrix-table w-full text-xs">
                                <thead>
                                    <tr>
                                        <th rowspan="2" class="bg-gray-100 w-8 border-r-2 border-gray-300 text-center px-1">Sec</th>
                                        <th colspan="4" class="bg-yellow-50 text-center border-r-2 border-gray-300 py-1">F1 (Digi)</th>
                                        <th colspan="4" class="bg-blue-50 text-center border-r-2 border-gray-300 py-1">F2 (Celcom)</th>
                                        <th colspan="4" class="bg-purple-50 text-center border-r-2 border-gray-300 py-1">F3 (MOCN)</th>
                                        ${type === 'upgrade' ? '<th rowspan="2" class="bg-gray-200 text-center w-12 border-l-2 border-gray-300 px-1">Cap%</th><th rowspan="2" class="bg-red-50 text-center border-l-2 border-red-200 w-32 px-1 leading-tight">Applied Case(s)</th>' : ''}
                                    </tr>
                                    <tr class="text-[10px] text-gray-600 uppercase tracking-tighter">
                                        ${bands.map(b => `<th class="text-center border-r border-gray-200 px-0 py-1">${b}</th>`).join('')}
                                        ${bands.map(b => `<th class="text-center border-r border-gray-200 px-0 py-1">${b}</th>`).join('')}
                                        ${bands.map((b,i) => `<th class="text-center ${i===3 && type!=='upgrade'?'':'border-r'} border-gray-200 px-0 py-1">${b}</th>`).join('')}
                                    </tr>
                                </thead>
                                <tbody>`;

                        Object.keys(data.sectors).sort().forEach(secId => {
                            const secData = data.sectors[secId];
                            const m = secData.matrix;
                            const shortSec = secId.split('_').pop() || '?';

                            tbl += `<tr><td class="font-bold text-blue-700 bg-gray-50 text-center border-r-2 border-gray-300 py-1.5">${shortSec}</td>`;

                            // Loop F1, F2, F3
                            ['F1', 'F2', 'F3'].forEach(f => {
                                bands.forEach((b, i) => {
                                    const cell = m[f] && m[f][b] ? m[f][b] : null;
                                    const isLastBand = (i === 3);
                                    const borderClass = isLastBand ? 'border-r-2 border-gray-300' : 'border-r border-gray-100';

                                    let val = '-', colorClass = '';

                                    if (cell) {
                                        const curr = cell.curr;
                                        const sugg = cell.sugg;
                                        const isDiff = curr !== sugg;

                                        if (type === 'current') {
                                            val = curr;
                                            if (isDiff) { colorClass = 'bg-red-50 text-red-600 font-extrabold'; }
                                            else { colorClass = 'text-gray-700 font-medium'; }
                                        } else {
                                            val = sugg;
                                            if (isDiff) { colorClass = 'bg-green-50 text-green-600 font-extrabold'; }
                                            else { colorClass = 'text-gray-700 font-medium'; }
                                        }
                                    }

                                    // Changed to text-[11px] and reduced padding to fit exactly
                                    tbl += `<td class="text-center ${borderClass} ${colorClass} px-0.5 py-1.5 text-[11px]" style="white-space:nowrap;">${val}</td>`;
                                });
                            });

                            if (type === 'upgrade') {
                                const pct = secData.capacity_pct;
                                const alert = pct > 73 ? 'text-red-600 font-extrabold' : 'text-green-600 font-extrabold';

                                tbl += `<td class="text-center ${alert} bg-gray-50 border-l-2 border-gray-300 text-xs tracking-tight px-1 py-1.5">${Number(pct).toFixed(2)}%</td>`;
                                tbl += `<td class="text-center text-red-600 font-bold bg-red-50 border-l-2 border-red-200 text-[10px] px-1 py-1.5 leading-tight" style="white-space:normal;">${secData.case_label || '-'}</td>`;
                            }
                            tbl += `</tr>`;
                        });

                        tbl += `</tbody></table></div>`;
                        return tbl;
                    };

                    const hasCongestion = Object.values(data.sectors).some(s => s.is_congested);

                    html += buildMatrix('Current Configuration', 'current');

                    if (hasCongestion) {
                        html += buildMatrix('Recommended Upgrade (Iterative)', 'upgrade');
                    } else {
                        html += `<div class="text-center text-green-600 font-bold text-xs py-2 bg-green-50 rounded mt-2 border border-green-200">
                                    <i class="fas fa-check-circle"></i> Capacity Healthy (All Sectors ≤ 73%)
                                 </div>`;
                    }

                    contentDiv.innerHTML = html;
                })
                .catch(err => { console.error(err); contentDiv.innerHTML = 'Error loading matrix.'; });
        }

        function createPopupContent(site, isCongestedRaw) {
            const siteIdDisplay = site.site_id || "UNKNOWN";
            const opName = (site.operator && site.operator !== 'Unknown') ? site.operator.toUpperCase() : "UNKNOWN";
            const host = site.coverage?.[0]?.host || 'Self';
            const isFemto = site.coverage?.[0]?.femto || false;

            let opClass = 'badge-normal';
            if (opName === 'CELCOM') opClass = 'badge-celcom';
            if (opName === 'DIGI') opClass = 'badge-digi';

            // Check if backend flagged ANY sector as congested (73% Upgrade Rule)
            let backendCongested = false;
            if (site.sectors) {
                backendCongested = site.sectors.some(s => s.is_congested);
            }
            // If EITHER raw KPI (80% rule) OR Backend (Upgrade rule) says congested -> RED BADGE
            const finalCongested = isCongestedRaw;

            const headerClass = finalCongested ? 'header-congested' : '';
            const statusBadge = finalCongested ? '<span class="badge badge-congested">CONGESTED</span>' : '<span class="badge badge-normal">HEALTHY</span>';

            // --- [FIX 1] Area Target & Mode Badges ---
            let areaBadge = '';
            let modeBadge = '';

            // Analyze Category (Urban/Outside)
            const areaStr = String(site.area_target || '').toLowerCase();
            const isUrban = areaStr.includes('urban') || areaStr.includes('kmc');

            if (isUrban) {
                areaBadge = '<span class="badge badge-urban">URBAN</span>';
            } else if (areaStr.includes('outside')) {
                areaBadge = '<span class="badge badge-outside">OUTSIDE</span>';
            } else {
                areaBadge = `<span class="badge bg-gray-500 text-white">${site.area_target || '-'}</span>`;
            }

            // Analyze Mode (BAU/NIC)
            const modeStr = String(site.bau_nic || '').toLowerCase();
            const isNIC = modeStr.includes('nic');

            if (isNIC) {
                modeBadge = '<span class="badge" style="background:#f97316; color:white; border:1px solid #fdba74;">NIC</span>';
            } else {
                modeBadge = '<span class="badge bg-gray-400 text-white">BAU</span>';
            }

            // --- [FIX 2] Dynamic Thresholds for Red Highlighting ---
            // PRB Threshold: 80% (Urban), 92% (Outside)
            const prbThreshold = isUrban ? 80.0 : 92.0;

            // Throughput Threshold: 5 (Urban BAU), 7 (Urban NIC), 3 (Outside)
            let thptThreshold = 3.0; // Default Outside
            if (isUrban) {
                thptThreshold = isNIC ? 7.0 : 5.0;
            }

            // GENERATE KPI TABLE
            let kpiRows = '';
            (site.sectors||[]).forEach(sec => {
                const secName = sec.name || "Unknown";
                const u = parseFloat(sec.users ?? 0);
                const p = parseFloat(sec.prb ?? 0);
                const t = parseFloat(sec.thpt ?? 0);
                const v = parseFloat(sec.vol ?? 0);

                // Apply dynamic checks using variables defined above
                const alertUser = (u >= 120) ? 'val-alert' : '';
                const alertPrb = (p >= prbThreshold) ? 'val-alert' : '';
                const alertThpt = (t > 0 && t < thptThreshold) ? 'val-alert' : '';

                kpiRows += `<tr>
                    <td class="text-left pl-3 font-bold text-gray-700 border-r border-gray-200">${secName}</td>
                    <td class="text-center border-r border-gray-200">${sec.month || '-'}</td>
                    <td class="text-center ${alertUser} border-r border-gray-200">${Math.round(u)}</td>
                    <td class="text-center ${alertPrb} border-r border-gray-200" title="Limit: ${prbThreshold}%">${p.toFixed(2)}</td>
                    <td class="text-center ${alertThpt} border-r border-gray-200" title="Limit: ${thptThreshold} Mbps">${t.toFixed(2)}</td>
                    <td class="text-center">${v.toFixed(2)}</td>
                </tr>`;
            });

            // GENERATE INITIAL MATRIX (No changes needed here, logic is static)
            const bands = ['L9', 'L18', 'L21', 'L26'];
            const matrixData = {};
            const grouping = {};

            // Step 1: Group by Sector -> Carrier -> Band -> Push Cells
            (site.band_matrix||[]).forEach(bmd => {
                const secName = bmd.sector || "Unknown";
                if(!grouping[secName]) grouping[secName] = {F1:{}, F2:{}, F3:{}};

                const carrier = (bmd.f1f2f3 || 'Other').toUpperCase();
                const rawBand = (bmd.band || '').toUpperCase();
                let mappedBand = null;

                if (rawBand.includes('2600') || rawBand.includes('26') || rawBand.includes('L26')) mappedBand = 'L26';
                else if (rawBand.includes('2100') || rawBand.includes('21') || rawBand.includes('L21')) mappedBand = 'L21';
                else if (rawBand.includes('1800') || rawBand.includes('18') || rawBand.includes('L18')) mappedBand = 'L18';
                else if (rawBand.includes('900') || rawBand.includes('L9')) mappedBand = 'L9';

                if (mappedBand && (carrier === 'F1' || carrier === 'F2' || carrier === 'F3')) {
                    if (!grouping[secName][carrier][mappedBand]) grouping[secName][carrier][mappedBand] = [];
                    grouping[secName][carrier][mappedBand].push({
                        cell: bmd.cell,
                        xtxr: (bmd.xtxr || '').toUpperCase()
                    });
                }
            });

            // Step 2: Calculate Counts based on Antenna Config / Cell quantity
            Object.keys(grouping).forEach(secId => {
                matrixData[secId] = {F1:{}, F2:{}, F3:{}};
                ['F1', 'F2', 'F3'].forEach(f => {
                    bands.forEach(b => {
                        const cells = grouping[secId][f][b];
                        if (cells && cells.length > 0) {
                            // Broaden the search parameters for Massive MIMO
                            const isMM = cells.some(c => 
                                c.xtxr.includes('32T') || 
                                c.xtxr.includes('64T') || 
                                c.xtxr.includes('MASSIVE') || 
                                c.xtxr.includes('MIMO')
                            );
                            
                            // Broaden the search parameters for Bi-Sector
                            const isExplicitBi = cells.some(c => 
                                c.xtxr.includes('2*') || 
                                c.xtxr.includes('2X') || 
                                c.xtxr.includes('BI')
                            );
            
                            let count = cells.length; // Default fallback
            
                            if (isMM) count = 4; // Massive MIMO = 4 Layers
                            else if (isExplicitBi) count = 2; // Tagged Bi-Sector = 2 Layers
            
                            matrixData[secId][f][b] = count;
                        } else {
                            matrixData[secId][f][b] = 0;
                        }
                    });
                });
            });

            // Step 3: Render Matrix Rows with the Layer Count
            const matrixRows = Object.keys(matrixData).sort().map(secId => {
                const parts = secId.split('_');
                const sectorNum = parts[parts.length-1] || '?';
                const sepStyle = "border-r-2 border-gray-400";

                let row = `<tr><td title="${secId}" class="font-bold text-blue-700 bg-gray-50 text-left pl-3 ${sepStyle}">Sect ${sectorNum}</td>`;

                ['F1', 'F2', 'F3'].forEach(f => {
                    bands.forEach((b, i) => {
                        const isLast = (i === bands.length - 1);
                        const border = isLast ? sepStyle : 'border-r border-gray-200';
                        const count = matrixData[secId][f][b];
                        const isActive = count > 0;

                        // Prints the actual count instead of '1'
                        row += `<td class="text-center ${isActive?'matrix-yes':'matrix-no'} ${border}">${isActive ? count : '0'}</td>`;
                    });
                });
                return row + '</tr>';
            }).join('') || `<tr><td colspan="13" class="text-center text-gray-400 italic py-4">No band data available</td></tr>`;

            const popupId = `popup_${siteIdDisplay.replace(/[^a-zA-Z0-9]/g, '_')}`;

            return `
                <div id="${popupId}">
                    <div class="popup-header ${headerClass}">
                        <div><div class="popup-title">${siteIdDisplay}</div><div class="popup-sub">${site.cluster||''} | Host: ${host} ${isFemto?'(FEMTO)':''}</div></div>
                        <div class="flex gap-2 items-center mt-1">
                            <span class="badge ${opClass}">${opName}</span>
                            ${statusBadge}
                            ${areaBadge} ${modeBadge} <span class="badge bg-gray-600 text-white">${site.region || 'REGION'}</span>
                        </div>
                        <button id="btn_${popupId}" onclick="togglePopupView('${popupId}', '${siteIdDisplay}')" class="header-btn">CONFIG & UPGRADE <i class="fas fa-arrow-right"></i></button>
                    </div>

                    <div id="kpi_${popupId}" class="p-4">
                        <div class="sector-table-container">
                            <table class="sector-table w-full text-xs">
                                <thead>
                                    <tr class="bg-gray-100 text-gray-600">
                                        <th class="text-left pl-3 py-2">Sector</th>
                                        <th class="text-center">Mth</th>
                                        <th class="text-center">Users</th>
                                        <th class="text-center">PRB%</th>
                                        <th class="text-center">Thpt</th>
                                        <th class="text-center">Vol</th>
                                    </tr>
                                </thead>
                                <tbody>${kpiRows}</tbody>
                            </table>
                        </div>
                        <div class="p-2 text-center mt-3 border-t"><button onclick="openPlotModal('${siteIdDisplay}')" class="bg-pink-500 hover:bg-pink-600 text-white text-xs font-bold py-1.5 px-4 rounded shadow transition transform hover:scale-105"><i class="fas fa-chart-line"></i> View Forecast Plots</button></div>
                    </div>

                    <div id="mat_${popupId}" class="p-4 hidden">
                        <div class="mb-2 text-[10px] font-bold text-gray-500 uppercase tracking-wider text-center">Active Bands (Layer Count)</div>
                        <div class="sector-table-container">
                            <table class="matrix-table text-xs">
                                <thead>
                                    <tr>
                                        <th rowspan="2" class="bg-gray-100 text-gray-700 w-16 text-left pl-3 border-r-2 border-gray-400">Sec</th>
                                        <th colspan="4" class="bg-yellow-100 text-yellow-800 text-center border-r-2 border-gray-400">F1 (Digi)</th>
                                        <th colspan="4" class="bg-blue-100 text-blue-800 text-center border-r-2 border-gray-400">F2 (Celcom)</th>
                                        <th colspan="4" class="bg-purple-100 text-purple-800 text-center border-r-2 border-gray-400">F3 (MOCN)</th>
                                    </tr>
                                    <tr class="text-[10px] text-gray-500 uppercase">
                                        ${bands.map((b,i) => `<th class="bg-yellow-50 text-center ${i===3?'border-r-2 border-gray-400':'border-r border-yellow-200'}">${b}</th>`).join('')}
                                        ${bands.map((b,i) => `<th class="bg-blue-50 text-center ${i===3?'border-r-2 border-gray-400':'border-r border-blue-200'}">${b}</th>`).join('')}
                                        ${bands.map((b,i) => `<th class="bg-purple-50 text-center ${i===3?'border-r-2 border-gray-400':'border-r border-purple-200'}">${b}</th>`).join('')}
                                    </tr>
                                </thead>
                                <tbody>${matrixRows}</tbody>
                            </table>
                        </div>
                    </div>

                    <div id="upg_${popupId}" class="p-4 hidden">
                        <div id="upg_content_${popupId}" class="sector-table-container">
                            <div class="text-center text-gray-400 py-4"><i class="fas fa-circle-notch fa-spin"></i> Loading Analysis...</div>
                        </div>
                    </div>
                </div>
            `;
        }

        function getAnnulusSector(lat, lng, azimuth, innerRadiusM, outerRadiusM, beamwidth) {
            const R = 6378137; const startAngle = (azimuth - beamwidth / 2) * (Math.PI / 180); const endAngle = (azimuth + beamwidth / 2) * (Math.PI / 180); const latRad = lat * (Math.PI / 180), lngRad = lng * (Math.PI / 180);
            function getPoint(radMeters, angle) { const d = radMeters / R; const pLat = Math.asin(Math.sin(latRad) * Math.cos(d) + Math.cos(latRad) * Math.sin(d) * Math.cos(angle)); const pLng = lngRad + Math.atan2(Math.sin(angle) * Math.sin(d) * Math.cos(latRad), Math.cos(d) - Math.sin(latRad) * Math.sin(pLat)); return [pLat * (180 / Math.PI), pLng * (180 / Math.PI)]; }
            const points = [];
            for (let i = 0; i <= 10; i++) points.push(getPoint(outerRadiusM, startAngle + (endAngle - startAngle) * (i / 10)));
            if (innerRadiusM > 0) { for (let i = 10; i >= 0; i--) points.push(getPoint(innerRadiusM, startAngle + (endAngle - startAngle) * (i / 10))); } else { points.push([lat, lng]); }
            return points;
        }

        function drawSiteCoverage(site, forceMapAdd = false) {
            if (!site.coverage) return;
            const azGroups = {};

            // FIX 1: Use sec.az instead of sec.azimuth to match Python data
            site.coverage.forEach(sec => {
                if(!azGroups[sec.az]) azGroups[sec.az] = {};
                azGroups[sec.az][sec.tech] = sec;
            });

            Object.entries(azGroups).forEach(([azStr, techs]) => {
                const az = parseFloat(azStr);
                const config = [
                    { tech: '5G', inner: 0, outer: 0.25, color: '#eab308', layer: layer5G },
                    { tech: '4G', inner: 0.25, outer: 0.50, color: '#3b82f6', layer: layer4G },
                    { tech: '3G', inner: 0.50, outer: 0.75, color: '#f97316', layer: layer3G },
                    { tech: '2G', inner: 0.75, outer: 1.00, color: '#6b7280', layer: layer2G }
                ];

                // FIX 2: Use t.rad instead of t.radius
                const maxRad = Math.max(...Object.values(techs).map(t => t.rad || 1000));

                config.forEach(c => {
                    if (techs[c.tech]) {
                        // FIX 3: Use the actual beamwidth (bw) from Python
                        const bw = techs[c.tech].bw || 65;
                        
                        // CORRECTED: Use maxRad * c.inner and maxRad * c.outer
                        const polyPoints = getAnnulusSector(site.lat, site.lng, az, maxRad * c.inner, maxRad * c.outer, bw);
                        
                        // CORRECTED: Use c.color
                        const poly = L.polygon(polyPoints, { 
                            color: c.color, 
                            fillColor: c.color, 
                            weight: 1, 
                            fillOpacity: 0.35 
                        });

                        // CORRECTED: Use c.layer
                        poly.addTo(c.layer);
                        if(forceMapAdd) { poly.addTo(map); activeCoverageLayers.push(poly); }
                    }
                });
            });
        }

        function clearCoverage() {
            activeCoverageLayers.forEach(l => map.removeLayer(l));
            activeCoverageLayers = [];
            if (connectionLines) { connectionLines.forEach(l => map.removeLayer(l)); connectionLines = []; }
        }
        map.on('click', (e) => clearCoverage());
        map.on('boxzoomend', function(e) { siteDataCache.forEach(site => { if (e.boxZoomBounds.contains([site.lat, site.lng])) drawSiteCoverage(site, true); }); });
        _attachCtxMenu(map);  // right-click → copy coordinates

        // ── Visible Site Count (viewport-driven, matching map.html) ──
        function updateVisibleSiteCount() {
            if (!siteDataCache || siteDataCache.length === 0) return;
            const bounds = map.getBounds();
            let visibleCount = 0;
            let visibleCongestedCount = 0;

            siteDataCache.forEach(site => {
                if (site && site.lat && site.lng && bounds.contains(L.latLng(site.lat, site.lng))) {
                    visibleCount++;

                    let isCongested = false;
                    if (site.sectors) {
                        const areaStr = String(site.area_target || '').toLowerCase();
                        const isUrban = areaStr.includes('urban') || areaStr.includes('kmc');
                        const prbThreshold = isUrban ? 80.0 : 92.0;

                        for (let sec of site.sectors) {
                            const p = parseFloat(sec.prb ?? 0);
                            if (p >= prbThreshold) {
                                isCongested = true;
                                break;
                            }
                        }
                    }

                    if (isCongested) {
                        visibleCongestedCount++;
                    }
                }
            });

            document.getElementById('siteCount').textContent = visibleCount.toLocaleString();
            document.getElementById('congCount').textContent = visibleCongestedCount.toLocaleString();
        }

        map.on('moveend', updateVisibleSiteCount);
        map.on('zoomend', updateVisibleSiteCount);

        function openPlotModal(siteId) { currentModalSiteId = siteId; document.getElementById('modalSiteIdDisplay').textContent = siteId; document.getElementById('plotModalOverlay').style.display = 'flex'; refreshPlot(); }
        function closePlotModal() { document.getElementById('plotModalOverlay').style.display = 'none'; document.getElementById('mapPlotContainer').innerHTML = ''; }
        async function refreshPlot() {
            if (!currentModalSiteId) return;
            const container = document.getElementById('mapPlotContainer'); const horizon = document.getElementById('modalHorizonSelect').value;
            container.innerHTML = `<div class="flex flex-col items-center justify-center h-full text-gray-500"><i class="fas fa-circle-notch fa-spin text-4xl mb-3 text-pink-500"></i><p class="font-semibold">Generating forecast...</p></div>`;
            try {
                const resp = await fetch(`/plot?site_id=${encodeURIComponent(currentModalSiteId)}&forecast_horizon=${horizon}`); const json = await resp.json();
                if (json.plot_image) { container.innerHTML = ''; Bokeh.embed.embed_item(JSON.parse(json.plot_image), "mapPlotContainer"); }
                else container.innerHTML = `<div class="text-red-500 font-bold p-10 text-center">No data found</div>`;
            } catch (err) { container.innerHTML = `<div class="text-red-500 font-bold p-10 text-center">Error loading plots.</div>`; }
        }

        function zoomToSite(id) { id = id.toUpperCase().trim(); if (siteMarkerMap[id]) { markers.zoomToShowLayer(siteMarkerMap[id], () => siteMarkerMap[id].openPopup()); }}

        function getClusterColor(id) { 
            // -1 is DBSCAN noise (points that don't belong to any cluster)
            if (id === -1 || id === "-1" || id === -1.0) return '#1e293b'; 
            
            // Convert to integer safely
            const intId = parseInt(id) || 0;

            // Use prime multipliers to heavily scramble the H, S, and L values.
            // This forces sequential IDs to jump wildly across the 3D color spectrum.
            const hue = (intId * 137.508) % 360;
            const saturation = 55 + ((intId * 43) % 45); // Bounces pseudo-randomly between 55% and 100%
            const lightness = 35 + ((intId * 27) % 40);  // Bounces pseudo-randomly between 35% and 75%
            
            return `hsl(${hue}, ${saturation}%, ${lightness}%)`; 
        }

		function getSignalColor(signalStr) {
            const signal = parseFloat(signalStr);
            if (signal >= -115) return '#facc15'; // Yellow (Weak)
            if (signal >= -120) return '#f97316'; // Orange (Poor)
            return '#dc2626'; // Red (Bad)
        }
        
        function loadCoverageHoles() {
            const weekStr = document.getElementById('weekSelect').value;
            let url = '/api/map/holes';
            if (weekStr && weekStr !== 'All') {
                url += `?week=${weekStr}`;
            }

            fetch(url).then(r => r.json()).then(data => {
                if (!data.features || data.features.length === 0) {
                    console.log("No coverage holes found in database.");
                    return;
                }

                var bounds = L.latLngBounds();
                var clusterLegendMap = {};
                layerSig100_120.clearLayers();
                layerSig121_130.clearLayers();
                layerSig131_worse.clearLayers();
                layerNoise.clearLayers();

                data.features.forEach(f => {
                    var props = f.properties;
                    var latlng = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
                    bounds.extend(latlng);

                    var color = getClusterColor(props.cluster);
                    var marker;

                    var safeCluster = String(props.cluster).trim();
                    if (safeCluster !== '-1' && safeCluster !== '') {
                        clusterLegendMap[safeCluster] = color;
                    }
                    var safeSource = String(props.data_source || "").trim().toUpperCase();
                    var sigVal = parseFloat(props.signal);

                    if (safeCluster === "-1") {
                        marker = L.circleMarker(latlng, { radius: 3, color: '#000', fillColor: '#000', fillOpacity: 0.8, weight: 1 }).addTo(layerNoise);
                    } else {
                        // We keep the shape so you still know the data source, but color it by Cluster ID
                        if (safeSource === "OOKLA") {
                            var ooklaIcon = L.divIcon({ className: 'custom-div-icon', html: `<div class="shape-triangle" style="border-bottom-color:${color};"></div>`, iconSize: [12, 12] });
                            marker = L.marker(latlng, { icon: ooklaIcon, clusterId: props.cluster, servingCell: props.serving_cell });
                        } else {
                            var mrIcon = L.divIcon({ className: 'custom-div-icon', html: `<div class="shape-square" style="background-color:${color};"></div>`, iconSize: [10, 10] });
                            marker = L.marker(latlng, { icon: mrIcon, clusterId: props.cluster, servingCell: props.serving_cell });
                        }

                        // Route the marker to the correct Layer Group based on Signal Strength
                        if (sigVal >= -120) {
                            marker.addTo(layerSig100_120);
                        } else if (sigVal >= -130) {
                            marker.addTo(layerSig121_130);
                        } else {
                            marker.addTo(layerSig131_worse);
                        }
                    }

                    marker.on('click', function() {
                        if (connectionLines) { connectionLines.forEach(l => map.removeLayer(l)); connectionLines = []; }

                        var servCell = props.serving_cell || "Unknown";
                        if (servCell === "Unknown" || !siteDataCache) {
                            marker.bindPopup(`<strong>Source: ${props.data_source}</strong><br>Serving Site: Unknown`).openPopup();
                            return;
                        }

                        var siteId = servCell.split('_')[0].split('-')[0].toUpperCase();
                        var servingSite = siteDataCache.find(s => s.site_id.toUpperCase() === siteId);

                        if (servingSite) {
                            var siteLatLng = L.latLng(servingSite.lat, servingSite.lng);
                            var holeLatLng = L.latLng(latlng[0], latlng[1]);
                            var distMeters = map.distance(holeLatLng, siteLatLng);
                            var distDisplay = distMeters > 1000 ? (distMeters/1000).toFixed(2) + ' km' : Math.round(distMeters) + ' m';

                            var singleLine = L.polyline([holeLatLng, siteLatLng], {
                                color: 'red', weight: 2, dashArray: '5, 5'
                            }).addTo(map);

                            connectionLines.push(singleLine);

                            marker.bindPopup(`
                                <div style="font-family: Inter, sans-serif;">
                                    <strong style="color: #1e40af; font-size: 14px;">Coverage Hole Info</strong><hr style="margin: 4px 0;">
                                    <b>Source:</b> ${props.data_source}<br>
                                    <b>Signal:</b> ${props.signal} dBm<br>
                                    <b>Cluster ID:</b> ${props.cluster}<br>
                                    <b>Serving Cell:</b> ${props.serving_cell}<br>
                                    <b style="color: #dc2626;">Distance to Site: ${distDisplay}</b>
                                </div>
                            `).openPopup();
                        } else {
                            marker.bindPopup(`<strong>Source: ${props.data_source}</strong><br>Site ${siteId} not found on map.`).openPopup();
                        }
                    });
                });

                // Ensure the map refreshes the new layers correctly
                if (map.hasLayer(layerSig100_120)) { map.removeLayer(layerSig100_120); map.addLayer(layerSig100_120); }
                if (map.hasLayer(layerSig121_130)) { map.removeLayer(layerSig121_130); map.addLayer(layerSig121_130); }
                if (map.hasLayer(layerSig131_worse)) { map.removeLayer(layerSig131_worse); map.addLayer(layerSig131_worse); }

                if (bounds.isValid()) { map.fitBounds(bounds, { padding: [50, 50], maxZoom: 14 }); }

                window._driveTestLegendClusters = Object.keys(clusterLegendMap).map(function (k) {
                    return { id: k, color: clusterLegendMap[k] };
                }).sort(function (a, b) {
                    return (parseInt(a.id, 10) || 0) - (parseInt(b.id, 10) || 0);
                });
                if (typeof updateMapLegend === 'function') updateMapLegend();

            }).catch(err => {
                console.error("Error loading coverage holes:", err);
            });
        }

        function setupMapAutocomplete() {
            const input = document.getElementById('siteSearchInput');
            const results = document.getElementById('siteSearchResults');
            input.addEventListener('input', async () => {
                const q = input.value.trim();
                if (q.length < 2) { results.classList.add('hidden'); return; }
                const res = await fetch(`/api/site_ids?q=${q}`);
                const data = await res.json();
                results.innerHTML = data.map(id => `<div class="autocomplete-item">${id}</div>`).join('');
                results.classList.remove('hidden');
                results.querySelectorAll('.autocomplete-item').forEach(item => {
                    item.addEventListener('click', () => {
                        input.value = item.textContent; results.classList.add('hidden'); zoomToSite(input.value);
                    });
                });
            });
        }

        function triggerZoom() { zoomToSite(document.getElementById('siteSearchInput').value); }

        var mapInitialized = false;
        function initMapIfNeeded() {
            if (mapInitialized) {
                map.invalidateSize();
                return;
            }
            mapInitialized = true;
            map.invalidateSize();
            init();
        }

        // --- PRICING JAVASCRIPT LOGIC ---

        // 1. Sector Specific Side-Panel
        window.openSectorPricingModal = function(sectorDataStr) {
            const sectors = JSON.parse(unescape(sectorDataStr));
            const panel = document.getElementById('sectorPricingPanel');
            const content = document.getElementById('sectorPricingContent');
            const isStaff = window.USER_DATA && window.USER_DATA.role === 'Staff';

            let html = '';
            let siteTotal = 0, siteTotalMin = 0, siteTotalMax = 0;

            Object.keys(sectors).sort().forEach(secId => {
                const sec = sectors[secId];
                if (!sec.capex || !sec.is_congested) return;
                const c = sec.capex;
                siteTotal += c.total_capex;

                c.eq_breakdown.forEach(eq => {
                    if (eq[2]) { siteTotalMin += eq[2].min; siteTotalMax += eq[2].max; }
                    else { siteTotalMin += eq[1]; siteTotalMax += eq[1]; }
                });
                if (c.es_chosen.range) { siteTotalMin += c.es_chosen.range.min; siteTotalMax += c.es_chosen.range.max; }
                else { siteTotalMin += c.es_chosen.cost; siteTotalMax += c.es_chosen.cost; }

                const shortSec = secId.split('_').pop() || '?';
                function fmtEQ(eq) {
                    if (isStaff && eq[2] && eq[2].min != null) return `RM ${eq[2].min.toLocaleString('en-MY',{minimumFractionDigits:2})} – RM ${eq[2].max.toLocaleString('en-MY',{minimumFractionDigits:2})}`;
                    return `RM ${eq[1].toLocaleString('en-MY',{minimumFractionDigits:2})}`;
                }
                function fmtES(es) {
                    if (isStaff && es.range && es.range.min != null) return `RM ${es.range.min.toLocaleString('en-MY',{minimumFractionDigits:2})} – RM ${es.range.max.toLocaleString('en-MY',{minimumFractionDigits:2})}`;
                    return `RM ${es.cost.toLocaleString('en-MY',{minimumFractionDigits:2})}`;
                }

                html += `<div class="bg-white border border-gray-200 rounded-lg p-3 mb-4 shadow-sm">
                    <div class="flex justify-between items-center border-b pb-2 mb-2">
                        <span class="font-bold text-blue-700">Sector ${shortSec}</span>
                        ${isStaff ? `<span class="text-xs font-semibold text-yellow-600 bg-yellow-50 border border-yellow-200 rounded px-2 py-0.5"><i class="fas fa-eye-slash mr-1"></i>Range View</span>` : `<span class="font-bold text-green-700">RM ${c.total_capex.toLocaleString('en-MY',{minimumFractionDigits:2})}</span>`}
                    </div>
                    <div class="text-[10px] text-gray-500 font-bold uppercase mb-1">Equipment (EQ) Needed:</div>
                    ${c.eq_breakdown.map(eq => `<div class="flex justify-between text-xs mb-1"><span class="text-gray-700">- ${eq[0]}</span><span class="font-semibold text-gray-600">${fmtEQ(eq)}</span></div>`).join('')}
                    <div class="text-[10px] text-gray-500 font-bold uppercase mt-3 mb-1">Engineering Service (ES) Applied:</div>
                    <div class="flex justify-between text-xs bg-green-50 p-1.5 rounded border border-green-100">
                        <span class="text-green-800 font-semibold">${c.es_chosen.name} (Highest)</span>
                        <span class="text-green-800 font-bold">${fmtES(c.es_chosen)}</span>
                    </div>
                </div>`;
            });

            if (html === '') html = '<div class="text-center text-gray-500 text-sm mt-10">No CAPEX required for this site.</div>';
            else {
                const totalHeader = isStaff
                    ? `<div class="text-xl font-bold text-green-400">RM ${siteTotalMin.toLocaleString('en-MY',{minimumFractionDigits:2})} – RM ${siteTotalMax.toLocaleString('en-MY',{minimumFractionDigits:2})}</div><div class="text-[10px] font-semibold text-yellow-400 mt-1"><i class="fas fa-info-circle mr-1"></i> Estimated range — exact pricing restricted</div>`
                    : `<div class="text-2xl font-bold text-green-400">RM ${siteTotal.toLocaleString('en-MY',{minimumFractionDigits:2})}</div>`;
                html = `<div class="bg-gradient-to-r from-gray-800 to-gray-700 text-white rounded-lg p-4 mb-4 shadow text-center"><div class="text-[10px] uppercase font-bold text-gray-400 mb-1">Total Estimated Site Capex</div>${totalHeader}</div>` + html;
            }
            content.innerHTML = html;
            panel.classList.remove('hidden');
        };

        window.openAdminPricing = function() {
            document.getElementById('adminPricingModal').classList.remove('hidden');
            fetch('/api/pricing').then(r => r.json()).then(data => {
                const role = window.USER_DATA && window.USER_DATA.role;
                const isStaff = role === 'Staff';

                document.getElementById('pricingModalTitle').innerHTML = isStaff
                    ? '<i class="fas fa-sliders-h mr-2"></i> Base Pricing'
                    : '<i class="fas fa-sliders-h mr-2"></i> Edit Base Pricing';
                document.getElementById('pricingSaveBtn').style.display = isStaff ? 'none' : '';
                document.getElementById('pricingCloseBtn').textContent = isStaff ? 'Close' : 'Cancel';

                function buildCategory(cat, catData, accentColor) {
                    const label = cat === 'EQ' ? 'Equipment (EQ) Costs' : 'Engineering Services (ES) Costs';
                    let html = `<div><h4 class="font-bold text-${accentColor}-800 border-b border-${accentColor}-200 pb-2 mb-3">${label}</h4><div class="space-y-4">`;
                    Object.entries(catData).forEach(([key, val]) => {
                        if (isStaff) {
                            const display = val.display || (val.min != null ? `RM ${val.min.toLocaleString('en-MY',{minimumFractionDigits:2})} – RM ${val.max.toLocaleString('en-MY',{minimumFractionDigits:2})}` : '—');
                            html += `<div class="flex items-center justify-between"><label class="text-xs font-semibold text-gray-600 w-1/2">${key}</label><span class="text-xs font-bold text-yellow-700 bg-yellow-50 border border-yellow-200 rounded px-2 py-1">${display}</span></div>`;
                        } else {
                            const safeId = key.replace(/[^a-zA-Z0-9]/g, '_');
                            const price = val.price != null ? val.price : val;
                            const pmin = val.min != null ? val.min : 0;
                            const pmax = val.max != null ? val.max : 0;
                            html += `<div class="border border-gray-100 rounded-lg p-2 bg-gray-50"><p class="text-xs font-bold text-gray-700 mb-2">${key}</p><div class="grid grid-cols-3 gap-2">
                                <div><p class="text-[10px] text-gray-500 font-semibold mb-1">Exact Price</p><div class="relative"><span class="absolute left-2 top-1.5 text-[10px] text-gray-400">RM</span><input type="number" min="0" data-orig-key="${key}" data-cat="${cat}" data-field="price" value="${price}" class="w-full border rounded pl-7 pr-1 py-1 text-xs font-bold text-gray-800 text-right focus:ring focus:ring-${accentColor}-200 outline-none bg-white"></div></div>
                                <div><p class="text-[10px] text-yellow-600 font-semibold mb-1">Staff Min</p><div class="relative"><span class="absolute left-2 top-1.5 text-[10px] text-gray-400">RM</span><input type="number" min="0" data-orig-key="${key}" data-cat="${cat}" data-field="min" value="${pmin}" class="w-full border border-yellow-200 rounded pl-7 pr-1 py-1 text-xs font-bold text-yellow-700 text-right focus:ring focus:ring-yellow-200 outline-none bg-yellow-50"></div></div>
                                <div><p class="text-[10px] text-yellow-600 font-semibold mb-1">Staff Max</p><div class="relative"><span class="absolute left-2 top-1.5 text-[10px] text-gray-400">RM</span><input type="number" min="0" data-orig-key="${key}" data-cat="${cat}" data-field="max" value="${pmax}" class="w-full border border-yellow-200 rounded pl-7 pr-1 py-1 text-xs font-bold text-yellow-700 text-right focus:ring focus:ring-yellow-200 outline-none bg-yellow-50"></div></div>
                            </div></div>`;
                        }
                    });
                    html += '</div></div>';
                    return html;
                }

                let html = '<div class="grid grid-cols-2 gap-6">';
                html += buildCategory('EQ', data.EQ || {}, 'blue');
                html += buildCategory('ES', data.ES || {}, 'green');
                html += '</div>';
                if (isStaff) html += '<p class="mt-4 text-xs text-center text-gray-400"><i class="fas fa-lock mr-1"></i>Exact pricing is restricted to Admin &amp; Planner roles.</p>';
                else html += '<p class="mt-3 text-xs text-center text-gray-400"><i class="fas fa-info-circle mr-1"></i><strong>Exact Price</strong> is used in internal calculations. <strong>Staff Min/Max</strong> is the range displayed to Staff users.</p>';
                document.getElementById('adminPricingFormArea').innerHTML = html;
            }).catch(e => {
                document.getElementById('adminPricingFormArea').innerHTML = '<div class="text-center text-red-500">Error loading pricing.</div>';
            });
        };

        window.saveAdminPricing = function() {
            const inputs = document.querySelectorAll('#adminPricingFormArea input');
            const payload = { EQ: {}, ES: {} };
            inputs.forEach(input => {
                const cat = input.getAttribute('data-cat');
                const key = input.getAttribute('data-orig-key');
                const field = input.getAttribute('data-field');
                if (!cat || !key || !field) return;
                if (!payload[cat][key]) payload[cat][key] = { price: 0, min: 0, max: 0 };
                payload[cat][key][field] = parseFloat(input.value) || 0;
            });
            for (const cat of ['EQ', 'ES']) {
                for (const [key, vals] of Object.entries(payload[cat])) {
                    if (vals.min > vals.max) { alert(`Validation error: Staff Min cannot exceed Staff Max for "${key}" (${cat}).`); return; }
                }
            }
            fetch('/api/pricing', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
            .then(r => r.json()).then(res => {
                if (res.success) { alert(res.message); document.getElementById('adminPricingModal').classList.add('hidden'); }
                else { alert('Error: ' + res.message); }
            });
        };


        // ==========================================
        // VIBE AI CHAT AGENT LOGIC
        // ==========================================
        function toggleChat() {
            // FIX: Updated to match the new global HTML IDs
            const panel = document.getElementById('chatPanel');
            const icon = document.querySelector('#chatToggleBtn i');

            if (panel.classList.contains('hidden')) {
                panel.classList.remove('hidden');
                panel.classList.add('flex');
                icon.classList.remove('fa-comment-dots');
                icon.classList.add('fa-times');
                document.getElementById('chatInput').focus();
            } else {
                panel.classList.add('hidden');
                panel.classList.remove('flex');
                icon.classList.remove('fa-times');
                icon.classList.add('fa-comment-dots');
            }
        }

        function appendMessage(text, sender) {
            const msgContainer = document.getElementById('chatMessages');
            const div = document.createElement('div');

            // Style based on who is sending the message
            if (sender === 'user') {
                div.className = 'self-end bg-blue-600 text-white p-3 rounded-tl-xl rounded-br-xl rounded-bl-xl shadow-sm max-w-[85%]';
            } else {
                // UPDATE THIS LINE BELOW:
                div.className = 'self-start bg-white border border-gray-200 text-gray-800 p-3 rounded-tr-xl rounded-br-xl rounded-bl-xl shadow-sm max-w-[95%] w-full';
            }

            // Simple Markdown parser to make bold text look nice
            let formattedText = text.replace(/\*\*(.*?)\*\*/g, '<strong class="text-blue-700">$1</strong>');
            formattedText = formattedText.replace(/\n/g, '<br>');

            div.innerHTML = formattedText;
            msgContainer.appendChild(div);
            msgContainer.scrollTop = msgContainer.scrollHeight;
        }

        async function handleChatSubmit(e) {
            e.preventDefault();
            const input = document.getElementById('chatInput');
            const message = input.value.trim();
            if (!message) return;

            // 1. Display User Message
            appendMessage(message, 'user');
            input.value = '';

            // 2. Display "Thinking..." Indicator
            const msgContainer = document.getElementById('chatMessages');
            const thinkingDiv = document.createElement('div');
            thinkingDiv.id = 'thinkingIndicator';
            thinkingDiv.className = 'self-start bg-gray-100 text-gray-500 p-3 rounded-tr-xl rounded-br-xl rounded-bl-xl shadow-sm text-xs flex items-center gap-2 border border-gray-200';
            thinkingDiv.innerHTML = '<i class="fas fa-circle-notch fa-spin text-blue-500"></i> Running Claude 4 Sonnet...';
            msgContainer.appendChild(thinkingDiv);
            msgContainer.scrollTop = msgContainer.scrollHeight;

            try {
                // 3. SECURE THE UI CONTEXT
                // Safely grab filters whether the user is on the Dashboard tab or the Map tab
                const currentWeek = document.getElementById('globalFilterWeek')?.value || document.getElementById('weekSelect')?.value || 'All';
                const currentRegion = document.getElementById('globalFilterRegion')?.value || document.getElementById('regionSelect')?.value || 'All';
                const currentOperator = document.getElementById('globalFilterOperator')?.value || 'All';
                const currentCluster = document.getElementById('globalFilterCluster')?.value || 'All';

                // 4. Request Data from Flask Backend with FULL CONTEXT
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        message: message, 
                        week: currentWeek,
                        region: currentRegion,
                        operator: currentOperator,
                        cluster: currentCluster
                    })
                });

                const data = await response.json();

                // Remove thinking indicator
                document.getElementById('thinkingIndicator').remove();

                if (response.ok) {
                    appendMessage(data.reply, 'ai');

                    // --- AGENTIC DEEP LINKING MAGIC ---
                    // Automatically fly the map to a site if the user types a valid site ID
                    const words = message.toUpperCase().split(' ');
                    words.forEach(word => {
                        const cleanWord = word.replace(/[^A-Z0-9]/g, ''); // Strip punctuation
                        if (siteMarkerMap && siteMarkerMap[cleanWord]) {
                            console.log("Agent triggered map zoom to:", cleanWord);
                            zoomToSite(cleanWord);
                        }
                    });
                } else {
                    appendMessage("Sorry, I encountered an error: " + (data.error || "Unknown error"), 'ai');
                }
            } catch (err) {
                document.getElementById('thinkingIndicator').remove();
                appendMessage("Connection failed. Make sure your Flask backend is running.", 'ai');
            }
        }

        // ==========================================
        // MAP ANNOTATIONS (LEAFLET.DRAW & API)
        // ==========================================
        // ==========================================
        // ANNOTATION SYSTEM — FULL IMPLEMENTATION
        // ==========================================

        // --- STATE ---
        let annPanelOpen = false;
        let currentDrawHandler = null;
        let pendingLayer = null;
        let annotationLayers = {};
        let allUsers = [];
        let currentAnnId = null;
        let hiddenAnnotations = new Set();
        let annotationsVisible = true;

        // Leaflet.draw feature group (replaces drawnItems)
        var drawnItems = new L.FeatureGroup();
        map.addLayer(drawnItems);
        var drawnItemsGenset = null; // initialised in initGensetMap

        // Returns the currently visible annotation map (coverage or genset)
        function _annMap() {
            const gc = document.getElementById('gensetContainer');
            return (typeof gensetMap !== 'undefined' && gensetMap && gc && gc.style.display !== 'none')
                ? gensetMap : map;
        }
        function _annDrawnItems() {
            return _annMap() === gensetMap ? drawnItemsGenset : drawnItems;
        }

        // --- HELPERS ---
        function statusBorderColor(status) {
            const m = { open: '#2563eb', in_progress: '#f97316', resolved: '#16a34a', closed: '#dc2626' };
            return m[status] || '#2563eb';
        }

        function timeAgo(isoString) {
            if (!isoString) return '';
            const now = Date.now();
            const then = new Date(isoString).getTime();
            const diffMs = now - then;
            if (diffMs < 0) return 'just now';
            const secs  = Math.floor(diffMs / 1000);
            const mins  = Math.floor(diffMs / 60000);
            const hours = Math.floor(diffMs / 3600000);
            const days  = Math.floor(diffMs / 86400000);
            if (secs  < 60) return 'just now';
            if (mins  < 60) return `${mins} minute${mins !== 1 ? 's' : ''} ago`;
            if (hours < 24) return `${hours} hour${hours !== 1 ? 's' : ''} ago`;
            return `${days} day${days !== 1 ? 's' : ''} ago`;
        }

        function escHtml(str) {
            if (!str) return '';
            return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
        }

        function updateAnnotationsToggleBtnVisibility() { /* no-op */ }

        // --- PANEL TOGGLE ---
        function toggleAnnotationPanel() {
            annPanelOpen = !annPanelOpen;
            const panel = document.getElementById('annotationPanel');
            const btn   = document.getElementById('annotationBtn');

            if (annPanelOpen) {
                // Close other panels
                const tasksPanel = document.getElementById('tasksPanel');
                if (tasksPanel && tasksPanel.classList.contains('open')) {
                    tasksPanel.classList.remove('open');
                    document.getElementById('tasksBtn').style.background = '#d97706';
                }
                const notesPanel = document.getElementById('notesPanel');
                if (notesPanel && notesPanel.classList.contains('open')) {
                    notesPanel.classList.remove('open');
                    document.getElementById('notesBtn').classList.remove('active');
                }
                panel.classList.add('open');
                btn.classList.add('active');
                setAnnTab('annotations');
                loadUsersForDropdowns();
            } else {
                panel.classList.remove('open');
                btn.classList.remove('active');
                cancelDraw();
            }
            updateAnnotationsToggleBtnVisibility();
        }

        function setAnnTab(tab) {
            loadAnnotations();
        }

        // --- DRAW TOOLS ---
        const drawHints = {
            marker:    'Click on the map to place a point.',
            polyline:  'Click to place waypoints. Double-click to finish the line.',
            polygon:   'Click to draw vertices. Click the first point to close the polygon.',
            rectangle: 'Click and drag to draw a rectangle.',
            circle:    'Click the center, then drag to set the buffer radius.'
        };

        function startDraw(type) {
            cancelDraw();
            document.querySelectorAll('.draw-tool-btn').forEach(b => b.classList.remove('active'));
            const hint = document.getElementById('drawHint');
            document.getElementById('drawHintText').textContent = drawHints[type] || 'Draw on the map.';
            hint.classList.remove('hidden');
            const options = { shapeOptions: { color: '#2563eb', fillColor: '#2563eb', fillOpacity: 0.2, weight: 2 } };
            const activeMap = _annMap();
            if      (type === 'marker')    currentDrawHandler = new L.Draw.Marker(activeMap, {});
            else if (type === 'polyline')  currentDrawHandler = new L.Draw.Polyline(activeMap, options);
            else if (type === 'polygon')   currentDrawHandler = new L.Draw.Polygon(activeMap, options);
            else if (type === 'rectangle') currentDrawHandler = new L.Draw.Rectangle(activeMap, options);
            else if (type === 'circle')    currentDrawHandler = new L.Draw.Circle(activeMap, options);
            if (currentDrawHandler) {
                currentDrawHandler.enable();
                const btnMap = { marker: 0, polyline: 1, polygon: 2, rectangle: 3, circle: 4 };
                const btns = document.querySelectorAll('.draw-tool-btn');
                if (btns[btnMap[type]]) btns[btnMap[type]].classList.add('active');
            }
        }

        function cancelDraw() {
            if (currentDrawHandler) {
                try { currentDrawHandler.disable(); } catch(e) {}
                currentDrawHandler = null;
            }
            if (pendingLayer) {
                try { _annMap().removeLayer(pendingLayer); } catch(e) {}
                pendingLayer = null;
            }
            const hint = document.getElementById('drawHint');
            if (hint) hint.classList.add('hidden');
            document.querySelectorAll('.draw-tool-btn').forEach(b => b.classList.remove('active'));
        }

        // Draw CREATED handler — shared by both maps
        function _onDrawCreated(e) {
            pendingLayer = e.layer;
            pendingLayer.addTo(_annMap());

            const layerType = e.layerType;
            const typeMap = { marker: 'point', polyline: 'polyline', polygon: 'polygon', rectangle: 'rectangle', circle: 'circle' };
            const shapeType = typeMap[layerType] || layerType;

            let geojson = null, centerLat = null, centerLng = null, radiusM = null;
            if (layerType === 'circle') {
                const c = pendingLayer.getLatLng();
                centerLat = c.lat; centerLng = c.lng;
                radiusM = pendingLayer.getRadius();
                geojson = JSON.stringify({ type: 'Point', coordinates: [c.lng, c.lat] });
            } else if (layerType === 'marker') {
                const ll = pendingLayer.getLatLng();
                geojson = JSON.stringify({ type: 'Point', coordinates: [ll.lng, ll.lat] });
            } else {
                geojson = JSON.stringify(pendingLayer.toGeoJSON().geometry);
            }

            document.getElementById('annCreateShapeType').value = shapeType;
            document.getElementById('annCreateGeoJSON').value = geojson;
            document.getElementById('annCreateCenterLat').value = centerLat || '';
            document.getElementById('annCreateCenterLng').value = centerLng || '';
            document.getElementById('annCreateRadius').value = radiusM || '';

            const shapeLabels = { point: 'Point marker', polyline: 'Line / route', polygon: 'Polygon area', rectangle: 'Rectangle area', circle: `Circle buffer (r=${radiusM ? Math.round(radiusM)+'m' : '?'})` };
            document.getElementById('annCreateShapeInfo').textContent = shapeLabels[shapeType] || shapeType;
            document.getElementById('annCreateTitleInput').value = '';
            document.getElementById('annCreateDesc').value = '';
            document.getElementById('annCreatePriority').value = 'normal';

            setAssignToggle('create', false);
            populateUserDropdown('annCreateAssignTo');
            document.getElementById('annCreateModal').classList.add('show');

            if (currentDrawHandler) { try { currentDrawHandler.disable(); } catch(e) {} currentDrawHandler = null; }
            const hint = document.getElementById('drawHint');
            if (hint) hint.classList.add('hidden');
            document.querySelectorAll('.draw-tool-btn').forEach(b => b.classList.remove('active'));
        }

        // Register on coverage map now; genset map registered in initGensetMap
        map.on(L.Draw.Event.CREATED, _onDrawCreated);

        // --- SAVE NEW ANNOTATION ---
        async function saveAnnotation() {
            const title = document.getElementById('annCreateTitleInput').value.trim();
            if (!title) { alert('Please enter a title for this annotation.'); return; }
            const assignedIds = getSelectedAssigneeIds('annCreateAssignTo');
            const payload = {
                title,
                description: document.getElementById('annCreateDesc').value.trim(),
                shape_type:  document.getElementById('annCreateShapeType').value,
                geojson:     document.getElementById('annCreateGeoJSON').value,
                center_lat:  document.getElementById('annCreateCenterLat').value || null,
                center_lng:  document.getElementById('annCreateCenterLng').value || null,
                radius_meters: document.getElementById('annCreateRadius').value || null,
                assigned_to_ids: assignedIds,
                assigned_to: assignedIds[0] || null,
                priority:    document.getElementById('annCreatePriority').value,
                color:       statusBorderColor('open'),
                fill_color:  document.getElementById('annCreateFillColor').value,
                status:      'open'
            };
            try {
                const res = await fetch('/api/annotations', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || 'Save failed');
                document.getElementById('annCreateModal').classList.remove('show');
                if (pendingLayer) { try { map.removeLayer(pendingLayer); } catch(e) {} pendingLayer = null; }
                loadAnnotations();
                loadNotes();
                loadTasks();
            } catch(e) { alert('Error saving annotation: ' + e.message); }
        }

        function cancelAnnotationCreate() {
            document.getElementById('annCreateModal').classList.remove('show');
            if (pendingLayer) { try { map.removeLayer(pendingLayer); } catch(e) {} pendingLayer = null; }
        }

        // --- LOAD & RENDER ANNOTATIONS LIST ---
        async function loadAnnotations() {
            const listEl = document.getElementById('annList');
            if (!listEl) return;
            listEl.innerHTML = '<div class="text-center text-gray-400 py-8 text-sm"><i class="fas fa-circle-notch fa-spin mr-2"></i> Loading...</div>';

            const status = document.getElementById('annStatusFilter')?.value || '';
            let url = '/api/annotations?';
            if (status) url += `status=${status}&`;

            try {
                const res = await fetch(url);
                const all = await res.json();
                const myUsername = window.USER_DATA && window.USER_DATA.username;

                // Annotations = has assignees OR created by me
                const anns = all.filter(a =>
                    (a.assignees && a.assignees.length > 0) ||
                    a.created_by_username === myUsername ||
                    a.is_rollout_completed_site
                );

                // Keep notes badge fresh
                const notes = all.filter(a =>
                    (!a.assignees || a.assignees.length === 0) &&
                    a.created_by_username === myUsername
                );
                const badge = document.getElementById('notesBadge');
                if (notes.length > 0) {
                    badge && (badge.textContent = notes.length, badge.classList.remove('hidden'));
                } else {
                    badge && badge.classList.add('hidden');
                }

                // Re-render all annotation layers on map
                renderAnnotationLayers(all);

                if (!anns.length) {
                    listEl.innerHTML = '<div class="text-center text-gray-400 py-8 text-sm"><i class="fas fa-map-marked-alt text-3xl mb-2"></i><br>No annotations found.</div>';
                    return;
                }

                listEl.innerHTML = anns.map(a => {
                    const shapeIcons = { point: '📍', polyline: '〰️', polygon: '⬡', rectangle: '⬜', circle: '⭕', buffer: '⭕' };
                    const icon = shapeIcons[a.shape_type] || '📐';
                    const priorityClass = a.priority !== 'normal' ? `priority-${a.priority}` : '';
                    const cardStyle = `border-left-color: ${statusBorderColor(a.status)}`;
                    const isHidden = hiddenAnnotations.has(a.id);
                    const hiddenClass = isHidden ? 'ann-card-hidden' : '';
                    const eyeIcon  = isHidden ? 'fa-eye-slash' : 'fa-eye';
                    const eyeTitle = isHidden ? 'Show on map' : 'Hide from map';
                    return `
                    <div id="ann-card-${a.id}" class="ann-card ${priorityClass} ${hiddenClass}" style="${cardStyle}" onclick="openDetailModal(${a.id})">
                        <div class="ann-card-title">
                            <span>${icon}</span>
                            <span class="flex-1 truncate">${escHtml(a.title)}${a.is_rollout_completed_site ? '<span class="inline-flex ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold bg-green-700 text-white">NEW SITE</span>' : ''}</span>
                            <span class="status-badge status-${a.status}">${a.status.replace('_',' ')}</span>
                            <button id="ann-eye-${a.id}" onclick="toggleAnnotationVisibility(${a.id}, event)" title="${eyeTitle}" class="ann-eye-btn ${isHidden ? 'ann-eye-hidden' : ''}"><i class="fas ${eyeIcon}"></i></button>
                        </div>
                        <div class="ann-card-meta">
                            <span><i class="fas fa-user text-gray-400 mr-1"></i>${escHtml(a.created_by_username)}</span>
                            ${(a.assignees && a.assignees.length)
                                ? `<span><i class="fas fa-arrow-right text-blue-400 mx-0.5"></i>${a.assignees.map(u => `<span class="inline-flex items-center bg-blue-100 text-blue-700 rounded-full px-1.5 py-0.5 text-[10px] font-semibold mr-0.5">${escHtml(u.full_name || u.username)}</span>`).join('')}</span>`
                                : ''}
                            ${a.comment_count > 0 ? `<span><i class="fas fa-comment text-gray-400 mr-1"></i>${a.comment_count}</span>` : ''}
                            <span class="ml-auto">${timeAgo(a.created_at)}</span>
                        </div>
                        ${a.description ? `<p class="text-[11px] text-gray-500 mt-1 truncate">${escHtml(a.description)}</p>` : ''}
                    </div>`;
                }).join('');
            } catch(e) {
                listEl.innerHTML = `<div class="text-center text-red-400 py-8 text-sm"><i class="fas fa-exclamation-triangle mr-2"></i>Failed to load: ${e.message}</div>`;
            }
        }

        // --- RENDER ANNOTATION LAYERS ON MAP ---
        function renderAnnotationLayers(anns) {
            // Clear from both maps
            Object.values(annotationLayers).forEach(l => {
                try { map.removeLayer(l); } catch(e) {}
                try { if (gensetMap) gensetMap.removeLayer(l); } catch(e) {}
            });
            annotationLayers = {};

            anns.forEach(a => {
                try {
                    const style = {
                        color:       statusBorderColor(a.status),
                        fillColor:   a.fill_color || '#2563eb',
                        fillOpacity: a.fill_opacity != null ? a.fill_opacity : 0.2,
                        weight:      a.stroke_weight || 2,
                        opacity:     0.9
                    };
                    let layer;
                    if (a.shape_type === 'circle' || a.shape_type === 'buffer') {
                        if (a.center_lat && a.center_lng && a.radius_meters) {
                            layer = L.circle([a.center_lat, a.center_lng], { radius: a.radius_meters, ...style });
                        }
                    } else if (a.geojson) {
                        const geom = JSON.parse(a.geojson);
                        if (a.shape_type === 'point') {
                            const coords = geom.coordinates;
                            const pinIcon = L.divIcon({
                                className: '',
                                html: `<div style="width:14px;height:14px;border-radius:50%;background:${a.fill_color||'#2563eb'};border:2.5px solid ${statusBorderColor(a.status)};box-shadow:0 2px 6px rgba(0,0,0,0.4);"></div>`,
                                iconSize: [14,14], iconAnchor: [7,7]
                            });
                            layer = L.marker([coords[1], coords[0]], { icon: pinIcon });
                        } else {
                            layer = L.geoJSON(geom, { style: () => style });
                        }
                    }
                    if (layer) {
                        layer.bindTooltip(`<b>${escHtml(a.title)}</b><br><span class="status-badge status-${a.status}">${a.status}</span>`, { sticky: true });
                        layer.on('click', () => openDetailModal(a.id));
                        if (!hiddenAnnotations.has(a.id)) {
                            layer.addTo(map);
                            if (gensetMap) layer.addTo(gensetMap);
                        }
                        annotationLayers[a.id] = layer;
                    }
                } catch(e) { console.warn('Could not render annotation', a.id, e); }
            });
        }

        // --- TOGGLE INDIVIDUAL ANNOTATION VISIBILITY ---
        function toggleAnnotationVisibility(id, e) {
            e.stopPropagation();
            if (hiddenAnnotations.has(id)) {
                hiddenAnnotations.delete(id);
                if (annotationLayers[id]) {
                    annotationLayers[id].addTo(map);
                    if (gensetMap) try { annotationLayers[id].addTo(gensetMap); } catch(ex) {}
                }
            } else {
                hiddenAnnotations.add(id);
                if (annotationLayers[id]) {
                    try { map.removeLayer(annotationLayers[id]); } catch(e) {}
                    try { if (gensetMap) gensetMap.removeLayer(annotationLayers[id]); } catch(e) {}
                }
            }
            const card   = document.getElementById('ann-card-' + id);
            const eyeBtn = document.getElementById('ann-eye-' + id);
            if (card) card.classList.toggle('ann-card-hidden', hiddenAnnotations.has(id));
            if (eyeBtn) {
                eyeBtn.innerHTML = hiddenAnnotations.has(id) ? '<i class="fas fa-eye-slash"></i>' : '<i class="fas fa-eye"></i>';
                eyeBtn.title     = hiddenAnnotations.has(id) ? 'Show on map' : 'Hide from map';
                eyeBtn.classList.toggle('ann-eye-hidden', hiddenAnnotations.has(id));
            }
        }

        // --- TOGGLE ALL ANNOTATIONS ---
        function toggleAllAnnotations() {
            const eyeBtn = document.getElementById('showAnnotationsBtn');
            annotationsVisible = !annotationsVisible;
            Object.entries(annotationLayers).forEach(([id, layer]) => {
                try {
                    if (annotationsVisible) {
                        if (!hiddenAnnotations.has(parseInt(id))) {
                            layer.addTo(map);
                            if (gensetMap) try { layer.addTo(gensetMap); } catch(ex) {}
                        }
                    } else {
                        map.removeLayer(layer);
                        if (gensetMap) try { gensetMap.removeLayer(layer); } catch(ex) {}
                    }
                } catch(e) {}
            });
            try {
                if (annotationsVisible) {
                    if (!map.hasLayer(drawnItems)) map.addLayer(drawnItems);
                    if (gensetMap && drawnItemsGenset && !gensetMap.hasLayer(drawnItemsGenset)) gensetMap.addLayer(drawnItemsGenset);
                } else {
                    if (map.hasLayer(drawnItems)) map.removeLayer(drawnItems);
                    if (gensetMap && drawnItemsGenset && gensetMap.hasLayer(drawnItemsGenset)) gensetMap.removeLayer(drawnItemsGenset);
                }
            } catch(e) {}
            if (eyeBtn) {
                eyeBtn.innerHTML = annotationsVisible ? '<i class="fas fa-eye"></i>' : '<i class="fas fa-eye-slash"></i>';
                eyeBtn.classList.toggle('annotations-hidden', !annotationsVisible);
                eyeBtn.title = annotationsVisible ? 'Hide annotation shapes' : 'Show annotation shapes';
            }
        }

        // --- DETAIL MODAL ROUTER ---
        // Decides whether to open the annotation edit modal or task resolution modal
        function openDetailModal(id) {
            // Check if this annotation is a task (has assignees and user is involved)
            const myUsername = window.USER_DATA && window.USER_DATA.username;
            // Look in allTasksData first — if it's a task, open the task resolution modal
            if (allTasksData && allTasksData.find(t => t.id === id)) {
                openTaskDetail(id);
            } else {
                // Otherwise open the standard annotation edit modal
                openAnnotationDetailModal(id);
            }
        }

        async function openAnnotationDetailModal(id) {
            currentAnnId = id;
            const res  = await fetch('/api/annotations');
            const anns = await res.json();
            const ann  = anns.find(a => a.id === id);
            if (!ann) return;

            document.getElementById('detailTitle').textContent    = ann.title;
            document.getElementById('detailMeta').textContent     = `Created by ${ann.created_by_username} · ${timeAgo(ann.created_at)}`;
            document.getElementById('detailAnnId').value          = id;
            document.getElementById('detailTitleInput').value     = ann.title;
            document.getElementById('detailDescInput').value      = ann.description || '';
            document.getElementById('detailStatus').value         = ann.status;
            document.getElementById('detailPriority').value       = ann.priority;
            document.getElementById('detailFillColor').value      = ann.fill_color || '#2563eb';

            await loadUsersForDropdowns();
            const currentAssigneeIds = (ann.assignees || []).map(a => a.id);
            setAssignToggle('detail', currentAssigneeIds.length > 0);
            populateUserDropdown('detailAssignTo', currentAssigneeIds);

            // Fly to layer on map
            if (annotationLayers[id]) {
                const layer = annotationLayers[id];
                if (layer.getBounds)  map.flyToBounds(layer.getBounds(), { padding: [80,80], maxZoom: 15 });
                else if (layer.getLatLng) map.flyTo(layer.getLatLng(), 14);
            }

            // Only show delete if current user is the creator
            const deleteBtn = document.querySelector('#annDetailModal .ann-modal-footer .btn-danger');
            if (deleteBtn) {
                const me = window.USER_DATA && window.USER_DATA.username;
                deleteBtn.style.display = (me && ann.created_by_username === me) ? '' : 'none';
            }

            loadComments(id);
            document.getElementById('annDetailModal').classList.add('show');
        }

        async function loadComments(id) {
            const thread = document.getElementById('commentThread');
            thread.innerHTML = '<div class="text-xs text-gray-400"><i class="fas fa-circle-notch fa-spin"></i> Loading...</div>';
            try {
                const res      = await fetch(`/api/annotations/${id}/comments`);
                const comments = await res.json();
                if (!comments.length) {
                    thread.innerHTML = '<div class="text-xs text-gray-400 italic">No comments yet. Be the first to comment.</div>';
                    return;
                }
                thread.innerHTML = comments.map(c => `
                    <div class="comment-bubble">
                        <div class="flex justify-between">
                            <span class="author">${escHtml(c.author_username)}</span>
                            <span class="time">${timeAgo(c.created_at)}</span>
                        </div>
                        <div class="body">${escHtml(c.body)}</div>
                    </div>`).join('');
                thread.scrollTop = thread.scrollHeight;
            } catch(e) {
                thread.innerHTML = '<div class="text-xs text-red-400">Failed to load comments.</div>';
            }
        }

        async function addComment() {
            const input = document.getElementById('newCommentInput');
            const body  = input.value.trim();
            if (!body || !currentAnnId) return;
            try {
                await fetch(`/api/annotations/${currentAnnId}/comments`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ body })
                });
                input.value = '';
                loadComments(currentAnnId);
            } catch(e) { alert('Failed to add comment'); }
        }

        async function updateAnnotation() {
            const id = document.getElementById('detailAnnId').value;
            const assignedIds = getSelectedAssigneeIds('detailAssignTo');
            const payload = {
                title:           document.getElementById('detailTitleInput').value,
                description:     document.getElementById('detailDescInput').value,
                assigned_to_ids: assignedIds,
                assigned_to:     assignedIds[0] || null,
                status:          document.getElementById('detailStatus').value,
                priority:        document.getElementById('detailPriority').value,
                color:           statusBorderColor(document.getElementById('detailStatus').value),
                fill_color:      document.getElementById('detailFillColor').value,
            };
            try {
                const res = await fetch(`/api/annotations/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (!res.ok) { const d = await res.json(); throw new Error(d.error); }
                closeDetailModal();
                loadAnnotations();
                loadTasks();
                const np = document.getElementById('notesPanel');
                if (np && np.classList.contains('open')) loadNotes();
            } catch(e) { alert('Update failed: ' + e.message); }
        }

        async function deleteAnnotation() {
            if (!confirm('Delete this annotation? This cannot be undone.')) return;
            const id = document.getElementById('detailAnnId').value;
            try {
                await fetch(`/api/annotations/${id}`, { method: 'DELETE' });
                closeDetailModal();
                loadAnnotations();
                loadTasks();
                const np = document.getElementById('notesPanel');
                if (np && np.classList.contains('open')) loadNotes();
            } catch(e) { alert('Delete failed: ' + e.message); }
        }

        function closeDetailModal() {
            document.getElementById('annDetailModal').classList.remove('show');
            currentAnnId = null;
        }

        // --- USER DROPDOWN HELPERS ---
        async function loadUsersForDropdowns() {
            try {
                const res = await fetch('/api/users/list');
                allUsers  = await res.json();
                populateUserDropdown('annCreateAssignTo');
                populateUserDropdown('detailAssignTo');
            } catch(e) { console.warn('Could not load users list', e); }
        }

        function populateUserDropdown(containerId, selectedIds = []) {
            const container = document.getElementById(containerId);
            if (!container) return;
            const selectedSet = new Set(selectedIds.map(String));
            if (!allUsers.length) {
                container.innerHTML = '<div class="text-xs text-gray-400 p-2 italic">No users found</div>';
                return;
            }
            container.innerHTML = allUsers.map(u => `
                <label class="flex items-center gap-2 px-3 py-1.5 hover:bg-blue-50 cursor-pointer text-xs text-gray-700 transition-colors">
                    <input type="checkbox" class="assignee-cb accent-blue-600 flex-shrink-0"
                           value="${u.id}" ${selectedSet.has(String(u.id)) ? 'checked' : ''}>
                    <span class="truncate">${escHtml(u.full_name || u.username)} <span class="text-gray-400">(${escHtml(u.role)})</span></span>
                </label>`).join('');
        }

        function getSelectedAssigneeIds(containerId) {
            const container = document.getElementById(containerId);
            if (!container) return [];
            return [...container.querySelectorAll('.assignee-cb:checked')].map(cb => parseInt(cb.value));
        }

        // --- ASSIGN TOGGLE ---
        function toggleAssignSection(modal) {
            const isCreate  = modal === 'create';
            const toggleBtn = document.getElementById(isCreate ? 'annCreateAssignToggle' : 'detailAssignToggle');
            const thumb     = document.getElementById(isCreate ? 'annCreateAssignThumb'  : 'detailAssignThumb');
            const section   = document.getElementById(isCreate ? 'annCreateAssignSection': 'detailAssignSection');
            const hint      = document.getElementById(isCreate ? 'annCreateAssignHint'   : 'detailAssignHint');
            const isOn  = toggleBtn.getAttribute('aria-pressed') === 'true';
            const nowOn = !isOn;
            toggleBtn.setAttribute('aria-pressed', String(nowOn));
            if (nowOn) {
                toggleBtn.classList.remove('bg-gray-300'); toggleBtn.classList.add('bg-blue-600');
                thumb.classList.remove(isCreate ? 'translate-x-1' : 'translate-x-0.5');
                thumb.classList.add(isCreate ? 'translate-x-6' : 'translate-x-5');
                section.classList.remove('hidden'); hint.classList.add('hidden');
            } else {
                toggleBtn.classList.remove('bg-blue-600'); toggleBtn.classList.add('bg-gray-300');
                thumb.classList.remove(isCreate ? 'translate-x-6' : 'translate-x-5');
                thumb.classList.add(isCreate ? 'translate-x-1' : 'translate-x-0.5');
                section.classList.add('hidden'); hint.classList.remove('hidden');
            }
        }

        function setAssignToggle(modal, on) {
            const isCreate  = modal === 'create';
            const toggleBtn = document.getElementById(isCreate ? 'annCreateAssignToggle' : 'detailAssignToggle');
            const current   = toggleBtn.getAttribute('aria-pressed') === 'true';
            if (current !== on) toggleAssignSection(modal);
        }

        // Sidebar Toggle Logic
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const toggleBtn = document.getElementById('sidebarToggle');
            const mapEl = document.getElementById('map');
            const layerCtrl = document.getElementById('mapLayerControl');

            sidebar.classList.toggle('collapsed');
            toggleBtn.classList.toggle('collapsed');
            mapEl.classList.toggle('expanded');

            const icon = toggleBtn.querySelector('i');
            const collapsed = sidebar.classList.contains('collapsed');
            if (collapsed) {
                icon.classList.replace('fa-chevron-left', 'fa-chevron-right');
                if (layerCtrl) layerCtrl.style.left = '16px';
            } else {
                icon.classList.replace('fa-chevron-right', 'fa-chevron-left');
                if (layerCtrl) layerCtrl.style.left = '366px';
            }
            // Sync bottom bar

            setTimeout(() => { map.invalidateSize(); }, 300);
        }

        // Sliding Panel Logic
        function togglePanel(panelId) {
            // Close all other panels first
            ['annotationPanel', 'notesPanel', 'tasksPanel'].forEach(id => {
                if (id !== panelId) document.getElementById(id).classList.remove('open');
            });
            // Toggle the selected panel
            document.getElementById(panelId).classList.toggle('open');
        }

        // ============================================================
        // NOTES LOGIC
        // ============================================================
        function openNotesPanel() { toggleNotesPanel(true); }

        function toggleNotesPanel(forceOpen = false) {
            const panel  = document.getElementById('notesPanel');
            const btn    = document.getElementById('notesBtn');
            const isOpen = panel.classList.contains('open');

            if (isOpen && !forceOpen) {
                panel.classList.remove('open');
                btn.classList.remove('active');
                return;
            }

            const annPanel = document.getElementById('annotationPanel');
            if (annPanel && annPanel.classList.contains('open')) {
                annPanel.classList.remove('open');
                document.getElementById('annotationBtn').classList.remove('active');
                if (typeof cancelDraw === 'function') cancelDraw();
            }
            const tasksPanel = document.getElementById('tasksPanel');
            if (tasksPanel && tasksPanel.classList.contains('open')) {
                tasksPanel.classList.remove('open');
                document.getElementById('tasksBtn').style.background = '#d97706';
            }

            panel.classList.add('open');
            btn.classList.add('active');
            loadNotes();
        }

        async function loadNotes() {
            const listEl = document.getElementById('noteList');
            listEl.innerHTML = '<div class="text-center text-gray-400 py-8 text-sm"><i class="fas fa-circle-notch fa-spin mr-2"></i> Loading notes...</div>';

            try {
                const res = await fetch('/api/annotations');
                const all = await res.json();
                const myUsername = window.USER_DATA && window.USER_DATA.username;

                const notes = all.filter(a => (!a.assignees || a.assignees.length === 0) && a.created_by_username === myUsername);

                document.getElementById('noteStatTotal').textContent    = notes.length;
                document.getElementById('noteStatOpen').textContent     = notes.filter(n => n.status === 'open').length;
                document.getElementById('noteStatResolved').textContent = notes.filter(n => n.status === 'resolved').length;

                const badge = document.getElementById('notesBadge');
                if (notes.length > 0) {
                    badge && (badge.textContent = notes.length, badge.classList.remove('hidden'));
                } else {
                    badge && badge.classList.add('hidden');
                }

                if (!notes.length) {
                    listEl.innerHTML = `
                    <div class="text-center text-purple-300 py-12 px-4">
                        <i class="fas fa-sticky-note text-4xl mb-3 block"></i>
                        <p class="text-sm font-semibold text-purple-400">No notes yet</p>
                        <p class="text-xs text-gray-400 mt-2">Draw a shape on the map and leave "Assign to someone?" toggled <b>off</b> to save it as a note.</p>
                    </div>`;
                    return;
                }

                const shapeIcons = { point: '📍', polyline: '〰️', polygon: '⬡', rectangle: '⬜', circle: '⭕', buffer: '⭕' };
                listEl.innerHTML = notes.map(n => {
                    const icon = shapeIcons[n.shape_type] || '📐';
                    const isHidden = hiddenAnnotations && hiddenAnnotations.has(n.id);
                    return `
                    <div class="note-card ${isHidden ? 'ann-card-hidden' : ''}" onclick="openAnnotationDetailModal(${n.id})">
                        <div class="ann-card-title">
                            <span>${icon}</span>
                            <span class="flex-1 truncate text-purple-900">${n.title}</span>
                            <span class="inline-flex items-center bg-purple-100 text-purple-700 rounded-full px-2 py-0.5 text-[10px] font-bold">Note</span>
                        </div>
                        <div class="ann-card-meta mt-1">
                            <span class="text-purple-500"><i class="fas fa-shapes mr-1 text-[10px]"></i>${n.shape_type}</span>
                            ${n.comment_count > 0 ? `<span><i class="fas fa-comment text-gray-400 mr-1"></i>${n.comment_count}</span>` : ''}
                            <span class="ml-auto">${timeAgo(n.created_at)}</span>
                        </div>
                        ${n.description ? `<p class="text-[11px] text-purple-600 mt-1 truncate">${n.description}</p>` : ''}
                    </div>`;
                }).join('');
            } catch(e) {
                listEl.innerHTML = `<div class="text-center text-red-400 py-8 text-sm">Failed to load: ${e.message}</div>`;
            }
        }

        // ============================================================
        // TASKS LOGIC
        // ============================================================
        let allTasksData = [];

        function toggleTasksPanel() {
            const panel = document.getElementById('tasksPanel');
            const btn   = document.getElementById('tasksBtn');
            const isOpen = panel.classList.contains('open');

            const annPanel = document.getElementById('annotationPanel');
            if (annPanel && annPanel.classList.contains('open') && !isOpen) {
                annPanel.classList.remove('open');
                const annBtn = document.getElementById('annotationBtn');
                if (annBtn) annBtn.classList.remove('active');
                if (typeof cancelDraw === 'function') cancelDraw();
            }
            const notesPanel = document.getElementById('notesPanel');
            if (notesPanel && notesPanel.classList.contains('open') && !isOpen) {
                notesPanel.classList.remove('open');
                document.getElementById('notesBtn').classList.remove('active');
            }

            panel.classList.toggle('open', !isOpen);
            btn.style.background = !isOpen ? '#b45309' : '#d97706';

            if (!isOpen) {
                loadTasks();
            } else {
                if (typeof toggleAllAnnotations === 'function' && typeof annotationsVisible !== 'undefined' && !annotationsVisible) toggleAllAnnotations();
            }
        }

        async function loadTasks() {
            const listEl = document.getElementById('taskList');
            listEl.innerHTML = '<div class="text-center text-gray-400 py-8 text-sm"><i class="fas fa-circle-notch fa-spin mr-2"></i> Loading tasks...</div>';

            try {
                const res  = await fetch('/api/annotations');
                if (!res.ok) throw new Error('API error ' + res.status);
                const all  = await res.json();

                const myUsername = window.USER_DATA.username;
                allTasksData = all.filter(a => {
                    const assigneeUsernames = (a.assignees || []).map(x => x.username);
                    const assignedToMe = assigneeUsernames.includes(myUsername) && a.created_by_username !== myUsername;
                    const assignedByMe = a.created_by_username === myUsername && (a.assignees || []).length > 0;
                    return assignedToMe || assignedByMe;
                });

                updateTaskStats(allTasksData);
                updateTaskBadge(allTasksData);
                renderTasks();
                if (typeof loadAnnotations === 'function') loadAnnotations();
            } catch (e) {
                listEl.innerHTML = `<div class="tasks-empty"><i class="fas fa-exclamation-circle text-red-400"></i><p class="text-sm font-semibold text-red-500 mt-2">Couldnot load tasks</p></div>`;
            }
        }

        function updateTaskStats(tasks) {
            document.getElementById('taskStatOpen').textContent       = tasks.filter(t => t.status === 'open').length;
            document.getElementById('taskStatInProgress').textContent = tasks.filter(t => t.status === 'in_progress').length;
            document.getElementById('taskStatResolved').textContent   = tasks.filter(t => t.status === 'resolved').length;
            document.getElementById('taskStatCritical').textContent   = tasks.filter(t => t.priority === 'critical').length;
        }

        function updateTaskBadge(tasks) {
            const active = tasks.filter(t => t.status === 'open' || t.status === 'in_progress').length;
            const badge  = document.getElementById('tasksBadge');
            if (active > 0) {
                badge.textContent = active > 99 ? '99+' : active;
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        }

        function renderTasks() {
            const statusF   = document.getElementById('taskStatusFilter').value;
            const priorityF = document.getElementById('taskPriorityFilter').value;
            const listEl    = document.getElementById('taskList');

            let filtered = allTasksData;
            if (statusF)   filtered = filtered.filter(t => t.status   === statusF);
            if (priorityF) filtered = filtered.filter(t => t.priority === priorityF);

            if (filtered.length === 0) {
                listEl.innerHTML = `<div class="tasks-empty"><i class="fas fa-clipboard-check"></i><p class="text-sm font-semibold text-gray-500 mt-2">No tasks found.</p></div>`;
                return;
            }

            const PRIO_ORDER = { critical: 0, high: 1, normal: 2, low: 3 };
            filtered.sort((a, b) => {
                const pd = (PRIO_ORDER[a.priority] ?? 2) - (PRIO_ORDER[b.priority] ?? 2);
                if (pd !== 0) return pd;
                return new Date(b.created_at) - new Date(a.created_at);
            });

            listEl.innerHTML = filtered.map(t => buildTaskCard(t)).join('');
        }

        function buildTaskCard(t) {
            const myUsername = window.USER_DATA.username;
            const assigneeUsernames = (t.assignees || []).map(x => x.username);
            const isAssignedToMe = assigneeUsernames.includes(myUsername);

            const statusLabels = { open: 'Open', in_progress: 'In Progress', resolved: 'Resolved', closed: 'Closed' };
            const statusClasses = { open: 'status-open', in_progress: 'status-in_progress', resolved: 'status-resolved', closed: 'status-closed' };

            const created = t.created_at ? new Date(t.created_at).toLocaleDateString('en-MY', { day:'2-digit', month:'short', year:'numeric' }) : '—';
            const daysOpen = t.days_open != null ? `${t.days_open}d` : (t.status === 'open' || t.status === 'in_progress') ? `${Math.floor((Date.now() - new Date(t.created_at)) / 86400000)}d open` : '';
            const commentCount = t.comment_count || 0;
            const hasLocation = t.representative_lat && t.representative_lng;
            const desc = t.description ? (t.description.length > 70 ? t.description.slice(0, 70) + '…' : t.description) : '<span class="italic text-gray-400">No description</span>';

            const assigneeChips = (t.assignees || []).map(u =>
                `<span class="inline-flex items-center bg-blue-100 text-blue-700 rounded-full px-1.5 py-0.5 text-[10px] font-semibold">${escHtml(u.full_name || u.username)}</span>`
            ).join(' ');

            const directionLabel = isAssignedToMe
                ? `<span class="task-assigner"><i class="fas fa-user-edit mr-0.5"></i> From: ${escHtml(t.created_by_username)}</span>`
                : `<span class="task-assigner" style="color:#1d4ed8;"><i class="fas fa-user-check mr-0.5"></i> To: ${assigneeChips}</span>`;

            return `
            <div class="task-card priority-${t.priority} status-${t.status}" onclick="openTaskDetail(${t.id})">
                <div class="task-card-title">
                    <span class="flex-1">${escHtml(t.title)}</span>
                    <span class="status-badge ${statusClasses[t.status] || ''}">${statusLabels[t.status] || t.status}</span>
                </div>
                <div class="task-card-desc">${desc}</div>
                <div class="task-card-meta">
                    <span class="task-priority-badge task-priority-${t.priority}">${t.priority}</span>
                    ${directionLabel}
                    <span><i class="fas fa-calendar-alt mr-0.5"></i> ${created}</span>
                    ${daysOpen ? `<span class="text-amber-600 font-semibold"><i class="fas fa-clock mr-0.5"></i> ${daysOpen}</span>` : ''}
                    ${commentCount > 0 ? `<span><i class="fas fa-comment mr-0.5 text-blue-400"></i> ${commentCount}</span>` : ''}
                    ${hasLocation ? `<button class="task-loc-btn" onclick="event.stopPropagation(); flyToTask(${t.representative_lat}, ${t.representative_lng})"><i class="fas fa-crosshairs mr-0.5"></i> View Map</button>` : ''}
                </div>
            </div>`;
        }

        function flyToTask(lat, lng) {
            map.flyTo([lat, lng], 15, { duration: 1.2 });
            setTimeout(() => {
                const panel = document.getElementById('tasksPanel');
                if (!panel.classList.contains('open')) panel.classList.add('open');
            }, 1400);
        }

        // ============================================================
        // TASK RESOLUTION WORKFLOW
        // ============================================================
        const TR_SUBMIT  = '🔵 SUBMIT:';
        const TR_APPROVE = '✅ APPROVED:';
        const TR_REJECT  = '❌ REJECTED:';

        let taskResAnn    = null;
        let taskResComments = [];

        // Alias used by Notes panel cards — opens the annotation detail/edit modal
        function openNoteDetail(annId) { openAnnotationDetailModal(annId); }

        async function openTaskDetail(annId) {
            const myUsername = window.USER_DATA.username;
            const resA = await fetch('/api/annotations');
            const all  = await resA.json();
            taskResAnn = all.find(a => a.id === annId);
            if (!taskResAnn) return;

            const resC = await fetch(`/api/annotations/${annId}/comments`);
            taskResComments = await resC.json();

            renderTaskResModal(myUsername);
            document.getElementById('taskResModal').classList.remove('hidden');
        }

        function closeTaskResModal() {
            document.getElementById('taskResModal').classList.add('hidden');
            taskResAnn = null;
            taskResComments = [];
        }

        function renderTaskResModal(myUsername) {
            const ann = taskResAnn;
            const isAssignee = (taskResAnn.assignees || []).some(a => a.username === myUsername);
            const isAssigner = taskResAnn.created_by_username  === myUsername;

            const headerColors = { critical: 'background: linear-gradient(135deg,#6d28d9,#7c3aed)', high: 'background: linear-gradient(135deg,#b91c1c,#dc2626)', normal: 'background: linear-gradient(135deg,#1e3a8a,#2563eb)', low: 'background: linear-gradient(135deg,#374151,#6b7280)'};
            document.getElementById('taskResHeader').style.cssText = (headerColors[ann.priority] || headerColors.normal) + '; padding:16px 24px; flex-shrink:0;';
            document.getElementById('taskResTitle').textContent = ann.title;
            const assigneeNames = (ann.assignees || []).map(a => a.full_name || a.username).join(', ') || ann.assigned_to_username || '—';
            document.getElementById('taskResMeta').textContent  = `Assigned by ${ann.created_by_username} → ${assigneeNames} · ${ann.priority} priority`;

            const stateInfo = resolveState(ann, taskResComments);
            const bannerColors = { open: 'background:#eff6ff; color:#1d4ed8;', in_progress: 'background:#fffbeb; color:#92400e;', pending_review: 'background:#f5f3ff; color:#6d28d9;', rejected: 'background:#fef2f2; color:#dc2626;', resolved: 'background:#f0fdf4; color:#15803d;', closed: 'background:#f0fdf4; color:#15803d;'};
            const bannerIcons = { open: 'fa-circle-dot', in_progress: 'fa-clock', pending_review: 'fa-hourglass-half', rejected: 'fa-times-circle', resolved: 'fa-check-circle', closed: 'fa-check-double' };
            const bannerLabels = { open: 'Open — awaiting work', in_progress: 'In Progress — work underway', pending_review: 'Pending Review — awaiting assigner approval',rejected: 'Rejected — assignee must resubmit', resolved: 'Resolved — approved by assigner', closed: 'Closed' };

            const banner = document.getElementById('taskResBanner');
            banner.style.cssText = (bannerColors[stateInfo.state] || '') + ' padding:8px 24px;';
            banner.innerHTML = `<i class="fas ${bannerIcons[stateInfo.state] || 'fa-info-circle'} mr-2"></i><span class="text-sm font-bold">${bannerLabels[stateInfo.state]|| stateInfo.state}</span>`;

            const timeline = document.getElementById('taskResTimeline');
            const resolutionComments = taskResComments.filter(c => c.body.startsWith(TR_SUBMIT) || c.body.startsWith(TR_APPROVE) || c.body.startsWith(TR_REJECT));
            if (!resolutionComments.length) {
                timeline.innerHTML = '<p class="text-xs text-gray-400 italic">No submissions yet.</p>';
            } else {
                timeline.innerHTML = resolutionComments.map(c => {
                    const isSubmit  = c.body.startsWith(TR_SUBMIT);
                    const isApprove = c.body.startsWith(TR_APPROVE);
                    const prefix    = isSubmit ? TR_SUBMIT : isApprove ? TR_APPROVE : TR_REJECT;
                    const text      = c.body.slice(prefix.length).trim();
                    const iconClass = isSubmit ? 'fa-paper-plane text-amber-500' : isApprove ? 'fa-check-double text-green-500' : 'fa-times-circle text-red-500';
                    const bg        = isSubmit ? 'bg-amber-50 border-amber-200' : isApprove ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200';
                    const label     = isSubmit ? 'Submitted by' : isApprove ? 'Approved by' : 'Rejected by';
                    return `
                    <div class="flex gap-3 p-3 rounded-lg border ${bg}">
                        <i class="fas ${iconClass} mt-0.5 flex-shrink-0"></i>
                        <div class="flex-1 min-w-0">
                            <div class="flex justify-between items-center mb-1">
                                <span class="text-xs font-bold text-gray-700">${label} <span class="text-blue-600">${c.author_username}</span></span>
                                <span class="text-[10px] text-gray-400 flex-shrink-0 ml-2">${timeAgo(c.created_at)}</span>
                            </div>
                            <p class="text-xs text-gray-600 leading-relaxed">${escHtml(text) || '<span class="italic text-gray-400">No message</span>'}</p>
                        </div>
                    </div>`;
                }).join('');
                timeline.scrollTop = timeline.scrollHeight;
            }

            const submitSec  = document.getElementById('taskResSubmitSection');
            const reviewSec  = document.getElementById('taskResReviewSection');
            const closedSec  = document.getElementById('taskResClosedSection');
            submitSec.classList.add('hidden'); reviewSec.classList.add('hidden'); closedSec.classList.add('hidden');
            document.getElementById('taskResReport').value   = ''; document.getElementById('taskResFeedback').value = '';

            if (stateInfo.state === 'resolved' || stateInfo.state === 'closed') {
                closedSec.classList.remove('hidden');
            } else if (stateInfo.state === 'pending_review' && isAssigner) {
                reviewSec.classList.remove('hidden');
            } else if ((stateInfo.state === 'open' || stateInfo.state === 'in_progress' || stateInfo.state === 'rejected') && isAssignee) {
                document.getElementById('taskResSubmitLabel').textContent = stateInfo.state === 'rejected' ? 'Resubmit Resolution Report' : 'Submit Resolution Report';
                submitSec.classList.remove('hidden');
            }
        }

        function resolveState(ann, comments) {
            if (ann.status === 'resolved' || ann.status === 'closed') return { state: 'resolved' };
            const resCmts = comments.filter(c => c.body.startsWith(TR_SUBMIT) || c.body.startsWith(TR_APPROVE) || c.body.startsWith(TR_REJECT));
            if (!resCmts.length) return { state: ann.status || 'open' };
            const last = resCmts[resCmts.length - 1];
            if (last.body.startsWith(TR_APPROVE)) return { state: 'resolved' };
            if (last.body.startsWith(TR_REJECT))  return { state: 'rejected' };
            if (last.body.startsWith(TR_SUBMIT))  return { state: 'pending_review' };
            return { state: ann.status || 'open' };
        }

        async function submitResolution() {
            const report = document.getElementById('taskResReport').value.trim();
            if (!report) { alert('Please write a report before submitting.'); return; }
            const annId = taskResAnn.id;
            try {
                await fetch(`/api/annotations/${annId}/comments`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body: `${TR_SUBMIT} ${report}` }) });
                await fetch(`/api/annotations/${annId}`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: taskResAnn.title, description: taskResAnn.description, assigned_to: taskResAnn.assigned_to, status: 'in_progress', priority: taskResAnn.priority, color: '#f97316', fill_color: taskResAnn.fill_color })
                });
                await refreshTaskRes(annId);
            } catch(e) { alert('Failed to submit: ' + e.message); }
        }

        async function reviewResolution(decision) {
            const feedback = document.getElementById('taskResFeedback').value.trim();
            if (decision === 'reject' && !feedback) { alert('Please provide a reason for rejection.'); return; }
            const annId = taskResAnn.id;
            const prefix = decision === 'approve' ? TR_APPROVE : TR_REJECT;
            const newStatus = decision === 'approve' ? 'resolved' : 'open';
            const newColor = decision === 'approve' ? '#16a34a' : '#2563eb';

            try {
                await fetch(`/api/annotations/${annId}/comments`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body: `${prefix} ${feedback}` }) });
                await fetch(`/api/annotations/${annId}`, {
                    method: 'PUT', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title: taskResAnn.title, description: taskResAnn.description, assigned_to: taskResAnn.assigned_to, status: newStatus, priority: taskResAnn.priority, color: newColor, fill_color: taskResAnn.fill_color })
                });
                await refreshTaskRes(annId);
                if (typeof loadTasks === 'function') loadTasks();
                if (typeof loadAnnotations === 'function') loadAnnotations();
                if (typeof loadNotes === 'function') loadNotes();
            } catch(e) { alert('Failed to submit review: ' + e.message); }
        }

        async function refreshTaskRes(annId) {
            const [resA, resC] = await Promise.all([ fetch('/api/annotations'), fetch(`/api/annotations/${annId}/comments`) ]);
            const all = await resA.json();
            taskResAnn      = all.find(a => a.id === annId);
            taskResComments = await resC.json();
            renderTaskResModal(window.USER_DATA.username);
        }

        // ===== UI RESIZING LOGIC =====
        (function() {
            const MIN_WIDTH = 260, MAX_WIDTH = 700;
            function makePanelResizable(panelId, handleId) {
                const panel  = document.getElementById(panelId);
                const handle = document.getElementById(handleId);
                if (!panel || !handle) return;
                let dragging = false, startX, startW;

                handle.addEventListener('mousedown', function(e) {
                    e.preventDefault(); dragging = true; startX = e.clientX; startW = panel.offsetWidth;
                    handle.classList.add('dragging'); document.body.style.cursor = 'ew-resize'; document.body.style.userSelect = 'none';
                });
                document.addEventListener('mousemove', function(e) {
                    if (!dragging) return;
                    const delta = startX - e.clientX;
                    panel.style.width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startW + delta)) + 'px';
                    panel.style.transition = 'transform 0.3s cubic-bezier(0.4,0,0.2,1)';
                });
                document.addEventListener('mouseup', function() {
                    if (!dragging) return;
                    dragging = false; handle.classList.remove('dragging'); document.body.style.cursor = ''; document.body.style.userSelect = '';
                });
            }
            makePanelResizable('annotationPanel', 'annotationResizeHandle');
            makePanelResizable('tasksPanel',      'tasksResizeHandle');
            makePanelResizable('notesPanel',      'notesResizeHandle');
        })();
        // ==========================================
        // ILLEGAL BITCOIN MINING ANALYSER
        // ==========================================
        let mhMap = null;
        let mhInitialized = false;
        let mhSitesData = [];
        let mhMode = 2;
        let mhSelected = [null, null, null];
        let mhAnalysisLayers = [];
        let mhSiteMarkerMap = {};
        const mhColors = ['#2563eb','#f59e0b','#7c3aed'];

        function mhDefaultIcon(){return L.divIcon({className:'custom-pin',html:'<div style="width:8px;height:8px;border-radius:50%;background:#2563eb;border:1.5px solid white;box-shadow:0 1px 4px rgba(0,0,0,0.3);"></div>',iconSize:[8,8],iconAnchor:[4,4]});}
        function mhSelectedIcon(c){return L.divIcon({className:'',html:'<div style="width:14px;height:14px;border-radius:50%;background:'+c+';border:2px solid white;box-shadow:0 0 10px '+c+';"></div>',iconSize:[14,14],iconAnchor:[7,7]});}

        async function initMinerHunter(){
            if(mhInitialized){mhMap.invalidateSize();return;}
            mhInitialized=true;
            mhMap=L.map('mhMap',{zoomControl:false,preferCanvas:true}).setView([3.8,108.5],6);
            L.control.zoom({ position: 'topright' }).addTo(mhMap);
            L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'&copy; Esri',maxZoom:19}).addTo(mhMap);
            L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,opacity:0.7}).addTo(mhMap);
            let data;
            if(siteDataCache&&siteDataCache.length>0){data=siteDataCache;}else{try{const r=await fetch('/api/sites');data=await r.json();if(!Array.isArray(data))data=[];}catch(e){data=[];}}
            mhSitesData=data.filter(s=>s&&s.site_id&&!isNaN(parseFloat(s.lat))&&!isNaN(parseFloat(s.lng))).map(s=>({site_id:s.site_id,site_name:s.site_name||s.site_id,lat:parseFloat(s.lat),lon:parseFloat(s.lng),state:s.region||'',district:s.cluster||''}));
            const cl=L.markerClusterGroup({disableClusteringAtZoom:14,maxClusterRadius:50,iconCreateFunction:function(c){return new L.DivIcon({html:'<div class="cluster-base cluster-normal"><span>'+c.getChildCount()+'</span></div>',className:'marker-cluster',iconSize:new L.Point(40,40)});} });
            mhSitesData.forEach(s=>{const m=L.marker([s.lat,s.lon],{icon:mhDefaultIcon()}).on('click',()=>mhSelectSite(s));mhSiteMarkerMap[s.site_id]=m;cl.addLayer(m);});
            mhMap.addLayer(cl);
            // Coverage
            if(siteDataCache&&siteDataCache.length>0){const cg=L.layerGroup().addTo(mhMap);siteDataCache.forEach(site=>{if(!site.coverage)return;const lat=parseFloat(site.lat),lng=parseFloat(site.lng);if(isNaN(lat)||isNaN(lng))return;const ag={};site.coverage.forEach(sec=>{const a=parseFloat(sec.az);if(!ag[a])ag[a]={};ag[a][sec.tech]=sec;});Object.entries(ag).forEach(([,techs])=>{const az=parseFloat(Object.keys(ag).find(k=>ag[k]===techs));const mr=Math.max(...Object.values(techs).map(t=>t.rad||1000));[{tech:'4G',i:0,o:0.5,c:'#3b82f6'},{tech:'3G',i:0.5,o:0.75,c:'#f97316'}].forEach(x=>{if(techs[x.tech]){const bw=techs[x.tech].bw||65,R=6378137,sA=(az-bw/2)*Math.PI/180,eA=(az+bw/2)*Math.PI/180,lR=lat*Math.PI/180,lgR=lng*Math.PI/180,pts=[];for(let i=0;i<=24;i++){const a=sA+(eA-sA)*(i/24);pts.push([(lR+(mr*x.o/R)*Math.cos(a))*180/Math.PI,(lgR+(mr*x.o/R)*Math.sin(a)/Math.cos(lR))*180/Math.PI]);}for(let i=24;i>=0;i--){const a=sA+(eA-sA)*(i/24);pts.push([(lR+(mr*x.i/R)*Math.cos(a))*180/Math.PI,(lgR+(mr*x.i/R)*Math.sin(a)/Math.cos(lR))*180/Math.PI]);}L.polygon(pts,{color:x.c,weight:0.5,fillOpacity:0.12}).addTo(cg);}});});});}
            mhSetMode(2);
        }

        function mhSetMode(m){mhMode=m;document.getElementById('mh_btn2pt').style.background=m===2?'#2563eb':'#f3f4f6';document.getElementById('mh_btn2pt').style.color=m===2?'white':'#4b5563';document.getElementById('mh_btn2pt').style.border=m===2?'none':'1px solid #d1d5db';document.getElementById('mh_btn3pt').style.background=m===3?'#2563eb':'#f3f4f6';document.getElementById('mh_btn3pt').style.color=m===3?'white':'#4b5563';document.getElementById('mh_btn3pt').style.border=m===3?'none':'1px solid #d1d5db';document.getElementById('mhSlot3Wrapper').style.display=m===2?'none':'';document.getElementById('mhStep3').style.display=m===2?'none':'';document.getElementById('mhSep23').style.display=m===2?'none':'';mhResetAll();}
        function mhSelectSite(s){const max=mhMode===2?2:3;const slot=mhSelected.slice(0,max).findIndex(x=>x===null);if(slot===-1)return;if(mhSelected.some(x=>x&&x.site_id===s.site_id))return;mhSelected[slot]=s;mhUpdateUI();mhMap.panTo([s.lat,s.lon]);}
        function mhRemoveSite(i){mhSelected[i]=null;const f=mhSelected.filter(s=>s);mhSelected=[f[0]||null,f[1]||null,f[2]||null];mhUpdateUI();mhClearAnalysis();}
        function mhUpdateUI(){const sc=['#2563eb','#f59e0b','#7c3aed'];[0,1,2].forEach(i=>{const el=document.getElementById('mhSlot'+(i+1)),s=mhSelected[i],c=sc[i];if(s){el.style.borderColor=c;el.style.background=c+'0a';el.innerHTML='<div style="width:10px;height:10px;border-radius:50%;background:'+c+';flex-shrink:0;"></div><div style="flex:1;min-width:0;"><div style="font-size:0.78rem;font-weight:700;color:#1e293b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'+s.site_id+'</div><div style="font-size:0.68rem;color:#9ca3af;">'+s.lat.toFixed(5)+', '+s.lon.toFixed(5)+'</div></div><span onclick="event.stopPropagation();mhRemoveSite('+i+')" style="cursor:pointer;color:#9ca3af;font-size:13px;padding:2px 4px;">✕</span>';}else{el.style.borderColor='#e5e7eb';el.style.background='#f9fafb';el.innerHTML='<div style="width:10px;height:10px;border-radius:50%;border:2px solid '+c+';flex-shrink:0;"></div><div style="flex:1;font-size:0.78rem;color:#9ca3af;font-style:italic;">Select site '+(i+1)+'</div>';}});mhSitesData.forEach(s=>{if(mhSiteMarkerMap[s.site_id])mhSiteMarkerMap[s.site_id].setIcon(mhDefaultIcon());});mhSelected.forEach((s,i)=>{if(s&&mhSiteMarkerMap[s.site_id])mhSiteMarkerMap[s.site_id].setIcon(mhSelectedIcon(mhColors[i]));});const count=mhSelected.filter(s=>s).length,needed=mhMode===2?2:3;[1,2,3].forEach(i=>{const el=document.getElementById('mhStep'+i);if(mhSelected[i-1]){el.style.borderColor=sc[i-1];el.style.background=sc[i-1];el.style.color='white';}else if(count===i-1){el.style.borderColor='#2563eb';el.style.background='transparent';el.style.color='#2563eb';}else{el.style.borderColor='#e5e7eb';el.style.background='transparent';el.style.color='#9ca3af';}});const rdy=mhSelected.slice(0,needed).every(s=>s!==null);const btn=document.getElementById('mhRunBtn');btn.disabled=!rdy;btn.style.background=rdy?'#16a34a':'#d1d5db';btn.style.color=rdy?'white':'#6b7280';btn.style.cursor=rdy?'pointer':'not-allowed';document.getElementById('mhMapHint').textContent=count<needed?'Click a cell site to select Site '+(count+1):needed+' sites selected — click Run Analysis';}
        function mhClearAnalysis(){mhAnalysisLayers.forEach(l=>mhMap.removeLayer(l));mhAnalysisLayers=[];document.getElementById('mhResults').innerHTML='<div style="font-size:0.78rem;color:#9ca3af;font-style:italic;">Run analysis to see results</div>';document.getElementById('mhOverpassStatus').style.display='none';}
        function mhResetAll(){mhSelected=[null,null,null];mhClearAnalysis();mhUpdateUI();}
        function mhHaversine(lat1,lon1,lat2,lon2){const R=6371,dLat=(lat2-lat1)*Math.PI/180,dLon=(lon2-lon1)*Math.PI/180;const a=Math.sin(dLat/2)**2+Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;return R*2*Math.asin(Math.sqrt(a));}
        function mhCircle(lat,lon,rKm,n=64){const R=6371,pts=[];for(let i=0;i<=n;i++){const a=2*Math.PI*i/n;pts.push([lat+(rKm/R)*180/Math.PI*Math.cos(a),lon+(rKm/R)*180/Math.PI*Math.sin(a)/Math.cos(lat*Math.PI/180)]);}return pts;}

        async function mhFetchOverpass(cLat,cLon,radiusKm){
            const st=document.getElementById('mhOverpassStatus'),tx=document.getElementById('mhOverpassText');
            st.style.display='block';tx.textContent='Fetching commercial/industrial buildings…';
            const rM=Math.max(radiusKm*1000,500);
            const bQ='[out:json][timeout:30];(way["building"~"commercial|industrial|warehouse|retail"](around:'+rM+','+cLat+','+cLon+'););out center;';
            const sQ='[out:json][timeout:30];(node["power"="substation"](around:'+(rM*3)+','+cLat+','+cLon+');way["power"="substation"](around:'+(rM*3)+','+cLat+','+cLon+'););out center;';
            let buildings=[],substations=[];
            try{const r=await fetch('https://overpass-api.de/api/interpreter',{method:'POST',body:'data='+encodeURIComponent(bQ)});const d=await r.json();buildings=(d.elements||[]).map(e=>({lat:e.center?.lat||e.lat,lon:e.center?.lon||e.lon,name:e.tags?.name||e.tags?.building||'Building',type:e.tags?.building||'commercial'})).filter(b=>b.lat&&b.lon);}catch(e){}
            tx.textContent='Fetching electrical substations…';
            try{const r=await fetch('https://overpass-api.de/api/interpreter',{method:'POST',body:'data='+encodeURIComponent(sQ)});const d=await r.json();substations=(d.elements||[]).map(e=>({lat:e.center?.lat||e.lat,lon:e.center?.lon||e.lon,name:e.tags?.name||'Substation'})).filter(s=>s.lat&&s.lon);}catch(e){}
            buildings.forEach(b=>{const dist=mhHaversine(b.lat,b.lon,cLat,cLon);const ic=L.divIcon({className:'',html:'<div style="width:8px;height:8px;background:#dc2626;border:1px solid white;box-shadow:0 0 4px #dc2626;"></div>',iconSize:[8,8],iconAnchor:[4,4]});const m=L.marker([b.lat,b.lon],{icon:ic}).bindPopup('<b style="color:#dc2626;">'+b.name+'</b><br>Type: '+b.type+'<br>Dist: '+dist.toFixed(3)+' km').addTo(mhMap);mhAnalysisLayers.push(m);});
            let nearestSub=null,nearestDist=Infinity;
            substations.forEach(s=>{const dist=mhHaversine(s.lat,s.lon,cLat,cLon);if(dist<nearestDist){nearestDist=dist;nearestSub=s;}const ic=L.divIcon({className:'',html:'<div style="font-size:12px;color:#ea580c;text-shadow:0 0 4px #ea580c;">★</div>',iconSize:[12,12],iconAnchor:[6,6]});const m=L.marker([s.lat,s.lon],{icon:ic}).bindPopup('<b style="color:#ea580c;">★ '+s.name+'</b><br>Dist: '+dist.toFixed(3)+' km').addTo(mhMap);mhAnalysisLayers.push(m);});
            if(nearestSub){const l=L.polyline([[cLat,cLon],[nearestSub.lat,nearestSub.lon]],{color:'#ea580c',weight:2,opacity:0.7,dashArray:'4,4'}).addTo(mhMap);mhAnalysisLayers.push(l);}
            tx.textContent=buildings.length+' buildings, '+substations.length+' substations found';
            setTimeout(()=>{st.style.display='none';},4000);
            return{buildings,substations,nearestSub,nearestDist};
        }

        async function mhRun(){
            mhClearAnalysis();document.getElementById('mhRunBtn').disabled=true;
            let cLat,cLon,maxDist,html='';
            if(mhMode===2){
                const s1=mhSelected[0],s2=mhSelected[1];cLat=(s1.lat+s2.lat)/2;cLon=(s1.lon+s2.lon)/2;
                const d1=mhHaversine(s1.lat,s1.lon,cLat,cLon),d2=mhHaversine(s2.lat,s2.lon,cLat,cLon);maxDist=Math.max(d1,d2);
                const td=mhHaversine(s1.lat,s1.lon,s2.lat,s2.lon);
                mhAnalysisLayers.push(L.polyline([[s1.lat,s1.lon],[s2.lat,s2.lon]],{color:'#eab308',weight:2,dashArray:'6,4'}).addTo(mhMap));
                mhAnalysisLayers.push(L.marker([cLat,cLon],{icon:L.divIcon({className:'',html:'<div style="width:13px;height:13px;background:#16a34a;border:2px solid white;border-radius:2px;transform:rotate(45deg);box-shadow:0 0 10px #16a34a;"></div>',iconSize:[13,13],iconAnchor:[6.5,6.5]})}).addTo(mhMap));
                mhAnalysisLayers.push(L.polygon(mhCircle(cLat,cLon,maxDist),{color:'#16a34a',weight:1.5,fillColor:'#16a34a',fillOpacity:.06,dashArray:'4,4'}).addTo(mhMap));
                mhMap.fitBounds(L.latLngBounds(mhCircle(cLat,cLon,maxDist)),{padding:[40,40]});
                html='<div style="font-size:0.65rem;padding:3px 8px;background:#eff6ff;color:#2563eb;border-radius:4px;display:inline-block;font-weight:700;margin-bottom:8px;">2-POINT</div>';
                html+='<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f1f5f9;font-size:0.78rem;"><span style="color:#6b7280;">S1 ↔ S2</span><span style="color:#16a34a;font-weight:700;">'+td.toFixed(3)+' km</span></div>';
                html+='<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f1f5f9;font-size:0.78rem;"><span style="color:#6b7280;">Buffer</span><span style="color:#16a34a;font-weight:700;">'+maxDist.toFixed(3)+' km</span></div>';
                html+='<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:0.78rem;"><span style="color:#6b7280;">Midpoint</span><span style="color:#16a34a;font-weight:700;font-size:0.7rem;">'+cLat.toFixed(4)+', '+cLon.toFixed(4)+'</span></div>';
            }else{
                const s1=mhSelected[0],s2=mhSelected[1],s3=mhSelected[2];const pts=[[s1.lat,s1.lon],[s2.lat,s2.lon],[s3.lat,s3.lon]];
                cLat=pts.reduce((s,p)=>s+p[0],0)/3;cLon=pts.reduce((s,p)=>s+p[1],0)/3;maxDist=Math.max(...pts.map(p=>mhHaversine(p[0],p[1],cLat,cLon)));
                mhAnalysisLayers.push(L.polygon(pts,{color:'#eab308',weight:2,fillColor:'#eab308',fillOpacity:.04,dashArray:'6,4'}).addTo(mhMap));
                mhAnalysisLayers.push(L.marker([cLat,cLon],{icon:L.divIcon({className:'',html:'<div style="width:13px;height:13px;background:#16a34a;border:2px solid white;border-radius:2px;transform:rotate(45deg);box-shadow:0 0 10px #16a34a;"></div>',iconSize:[13,13],iconAnchor:[6.5,6.5]})}).addTo(mhMap));
                mhAnalysisLayers.push(L.polygon(mhCircle(cLat,cLon,maxDist),{color:'#16a34a',weight:1.5,fillColor:'#16a34a',fillOpacity:.06,dashArray:'4,4'}).addTo(mhMap));
                mhColors.forEach((col,i)=>{mhAnalysisLayers.push(L.polyline([pts[i],[cLat,cLon]],{color:col,weight:1,opacity:.4,dashArray:'3,5'}).addTo(mhMap));});
                mhMap.fitBounds(L.latLngBounds(mhCircle(cLat,cLon,maxDist)),{padding:[40,40]});
                const d12=mhHaversine(s1.lat,s1.lon,s2.lat,s2.lon),d23=mhHaversine(s2.lat,s2.lon,s3.lat,s3.lon),d13=mhHaversine(s1.lat,s1.lon,s3.lat,s3.lon);
                html='<div style="font-size:0.65rem;padding:3px 8px;background:#f5f3ff;color:#7c3aed;border-radius:4px;display:inline-block;font-weight:700;margin-bottom:8px;">3-POINT</div>';
                html+='<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f1f5f9;font-size:0.78rem;"><span style="color:#6b7280;">Buffer</span><span style="color:#16a34a;font-weight:700;">'+maxDist.toFixed(3)+' km</span></div>';
                html+='<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f1f5f9;font-size:0.78rem;"><span style="color:#6b7280;">Centroid</span><span style="color:#16a34a;font-weight:700;font-size:0.7rem;">'+cLat.toFixed(4)+', '+cLon.toFixed(4)+'</span></div>';
                html+='<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f1f5f9;font-size:0.78rem;"><span style="color:#6b7280;">S1↔S2</span><span style="font-weight:700;">'+d12.toFixed(3)+' km</span></div>';
                html+='<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f1f5f9;font-size:0.78rem;"><span style="color:#6b7280;">S2↔S3</span><span style="font-weight:700;">'+d23.toFixed(3)+' km</span></div>';
                html+='<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:0.78rem;"><span style="color:#6b7280;">S1↔S3</span><span style="font-weight:700;">'+d13.toFixed(3)+' km</span></div>';
            }
            const ov=await mhFetchOverpass(cLat,cLon,maxDist);
            html+='<div style="margin-top:8px;padding-top:8px;border-top:1px solid #e5e7eb;">';
            html+='<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f1f5f9;font-size:0.78rem;"><span style="color:#6b7280;">Comm./Ind. buildings</span><span style="color:#dc2626;font-weight:700;">'+ov.buildings.length+'</span></div>';
            html+='<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #f1f5f9;font-size:0.78rem;"><span style="color:#6b7280;">Substations</span><span style="color:#ea580c;font-weight:700;">'+ov.substations.length+'</span></div>';
            if(ov.nearestSub)html+='<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:0.78rem;"><span style="color:#6b7280;">Nearest substation</span><span style="color:#ea580c;font-weight:700;">'+ov.nearestDist.toFixed(3)+' km</span></div>';
            html+='</div>';
            document.getElementById('mhResults').innerHTML=html;
            document.getElementById('mhRunBtn').disabled=false;mhUpdateUI();
        }

        function mhOnSearch(q){const rd=document.getElementById('mhSearchResults');if(q.length<2){rd.style.display='none';return;}const m=mhSitesData.filter(s=>s.site_id.toLowerCase().includes(q.toLowerCase())||s.site_name.toLowerCase().includes(q.toLowerCase())).slice(0,8);if(!m.length){rd.style.display='none';return;}rd.innerHTML=m.map(s=>'<div class="autocomplete-item" onclick="mhSearchSelect(\''+s.site_id+'\')"><span style="font-weight:700;color:#2563eb;">'+s.site_id+'</span><br><span style="font-size:0.75rem;color:#9ca3af;">'+s.state+' · '+s.district+'</span></div>').join('');rd.style.display='block';}
        function mhSearchSelect(id){const s=mhSitesData.find(x=>x.site_id===id);if(s){mhMap.setView([s.lat,s.lon],14);mhSelectSite(s);}document.getElementById('mhSearchResults').style.display='none';document.getElementById('mhSearchBox').value='';}


        // ==========================================
        // CCTV PLANNING MAP + PIPELINE
        // ==========================================
        let cctvMap = null;
        let cctvInitialized = false;
        let cctvSiteDataCache = [];
        let cctvDrawControl = null;
        let cctvAoiLayer = null;
        let cctvResultLayers = L.layerGroup();
        let cctvCoverageGroup = null;
        let cctvSiteMarkers = null;
        let cctvIsDrawing = false;

        // Uploaded data stores
        const cctvInputs = {
            building: { geojson: null, layer: null, color: '#6366f1' },
            parking:  { geojson: null, layer: null, color: '#0891b2' },
            poles:    { geojson: null, layer: null, color: '#d97706' },
            camera:   { csv: null, rows: null, layer: null, color: '#dc2626' },
            offset:   { csv: null, rows: null, layer: null, color: '#7c3aed' }
        };

        // CSV parser for camera and offset tables
        function cctvCsvLoaded(input, type) {
            const file = input.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const text = e.target.result.trim();
                    const lines = text.split(/\r?\n/);
                    if (lines.length < 2) throw new Error('CSV needs a header row and at least one data row');
                    const headers = lines[0].split(',').map(h => h.trim());
                    const rows = [];
                    for (let i = 1; i < lines.length; i++) {
                        if (!lines[i].trim()) continue;
                        const vals = lines[i].split(',').map(v => v.trim());
                        const row = {};
                        headers.forEach((h, idx) => { row[h] = vals[idx] || ''; });
                        rows.push(row);
                    }

                    cctvInputs[type].csv = text;
                    cctvInputs[type].rows = rows;

                    const label = document.getElementById('cctv' + type.charAt(0).toUpperCase() + type.slice(1) + 'Label');
                    document.getElementById('cctv' + type.charAt(0).toUpperCase() + type.slice(1) + 'Clear').style.display = '';

                    if (type === 'camera') {
                        const info = rows.map(r => (r.camera_type || '?') + ': ' + (r.hfov_deg||'?') + '°/' + (r.range_m||'?') + 'm/RM' + (r.unit_price_rm||'?')).join('; ');
                        label.textContent = file.name + ' (' + rows.length + ' types) — ' + info;
                    } else {
                        const info = rows.map(r => r.offset + '°').join(', ');
                        label.textContent = file.name + ' (' + rows.length + ' offsets) — ' + info;
                    }

                    cctvUpdateUploadSummary();
                    setCctvStatus(type.charAt(0).toUpperCase() + type.slice(1) + ' CSV loaded — ' + rows.length + ' rows', 'fas fa-check-circle', 3000);
                } catch (err) {
                    alert('Error reading ' + type + ' CSV: ' + err.message);
                }
            };
            reader.readAsText(file);
        }

        function cctvFileLoaded(input, type) {
            const file = input.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const gj = JSON.parse(e.target.result);
                    if (!gj.type || !gj.features) throw new Error('Invalid GeoJSON');
                    cctvInputs[type].geojson = gj;

                    const label = document.getElementById('cctv' + type.charAt(0).toUpperCase() + type.slice(1) + 'Label');
                    label.textContent = file.name + ' (' + gj.features.length + ' features)';
                    document.getElementById('cctv' + type.charAt(0).toUpperCase() + type.slice(1) + 'Clear').style.display = '';

                    // Draw on map
                    if (cctvInputs[type].layer) { cctvMap.removeLayer(cctvInputs[type].layer); }
                    const style = type === 'poles'
                        ? { pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 4, color: cctvInputs[type].color, fillColor: cctvInputs[type].color, fillOpacity: 0.7, weight: 1 }) }
                        : { style: { color: cctvInputs[type].color, weight: 2, fillOpacity: 0.15 } };

                    cctvInputs[type].layer = L.geoJSON(gj, style).addTo(cctvMap);
                    cctvMap.fitBounds(cctvInputs[type].layer.getBounds(), { padding: [40, 40] });

                    cctvUpdateUploadSummary();
                    setCctvStatus(type.charAt(0).toUpperCase() + type.slice(1) + ' loaded — ' + gj.features.length + ' features', 'fas fa-check-circle', 3000);
                } catch (err) {
                    alert('Error reading ' + type + ' GeoJSON: ' + err.message);
                }
            };
            reader.readAsText(file);
        }

        function cctvClearFile(type) {
            if (cctvInputs[type].geojson !== undefined) cctvInputs[type].geojson = null;
            if (cctvInputs[type].csv !== undefined) { cctvInputs[type].csv = null; cctvInputs[type].rows = null; }
            if (cctvInputs[type].layer) { cctvMap.removeLayer(cctvInputs[type].layer); cctvInputs[type].layer = null; }
            const label = document.getElementById('cctv' + type.charAt(0).toUpperCase() + type.slice(1) + 'Label');
            label.textContent = 'Choose file…';
            document.getElementById('cctv' + type.charAt(0).toUpperCase() + type.slice(1) + 'Clear').style.display = 'none';
            document.getElementById('cctv' + type.charAt(0).toUpperCase() + type.slice(1) + 'File').value = '';
            cctvUpdateUploadSummary();
        }

        function cctvUpdateUploadSummary() {
            const el = document.getElementById('cctvUploadSummary');
            const txt = document.getElementById('cctvUploadSummaryText');
            const required = ['building', 'parking', 'poles', 'camera', 'offset'];
            const loaded = required.filter(r => {
                if (r === 'camera' || r === 'offset') return cctvInputs[r].csv !== null;
                return cctvInputs[r].geojson !== null;
            });
            if (loaded.length === 0) { el.style.display = 'none'; return; }
            el.style.display = 'block';
            const missing = required.filter(r => !loaded.includes(r));
            if (missing.length === 0) {
                txt.textContent = 'All 5 inputs loaded — ready to run pipeline';
                el.style.background = '#d1fae5'; el.style.borderColor = '#6ee7b7'; el.style.color = '#065f46';
            } else {
                txt.textContent = loaded.length + '/5 loaded. Missing: ' + missing.join(', ');
                el.style.background = '#fef3c7'; el.style.borderColor = '#fde68a'; el.style.color = '#92400e';
            }
        }

        function setCctvStatus(msg, icon, autohide) {
            const el = document.getElementById('cctvStatus');
            const txt = document.getElementById('cctvStatusText');
            const spin = document.getElementById('cctvStatusSpinner');
            if (!el) return;
            txt.textContent = msg;
            spin.className = icon || 'fas fa-circle-notch fa-spin';
            el.style.opacity = '1';
            if (autohide) setTimeout(() => { el.style.opacity = '0'; }, autohide);
        }

        async function initCctvMap() {
            if (cctvInitialized) { cctvMap.invalidateSize(); return; }
            cctvInitialized = true;
            setCctvStatus('Initializing map…', 'fas fa-circle-notch fa-spin');

            cctvMap = L.map('cctvMap', { center: [3.1390, 101.6869], zoom: 12, zoomControl: false });
            L.control.zoom({ position: 'topright' }).addTo(cctvMap);
            L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { attribution: '&copy; Esri', maxZoom: 19 }).addTo(cctvMap);
            L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, opacity: 0.7 }).addTo(cctvMap);

            cctvCoverageGroup = L.layerGroup().addTo(cctvMap);
            cctvResultLayers = L.layerGroup().addTo(cctvMap);

            // Leaflet.draw for AOI
            const drawnItems = new L.FeatureGroup().addTo(cctvMap);
            cctvMap.on(L.Draw.Event.CREATED, function(e) {
                drawnItems.clearLayers();
                cctvAoiLayer = e.layer;
                drawnItems.addLayer(cctvAoiLayer);
                cctvAoiLayer.setStyle({ color: '#22c55e', weight: 2, fillColor: '#22c55e', fillOpacity: 0.1, dashArray: '6,4' });
                document.getElementById('cctvAoiInfo').style.display = 'block';
                const bounds = cctvAoiLayer.getBounds();
                const areaSqM = L.GeometryUtil ? 0 : 0; // approximate
                document.getElementById('cctvAoiInfoText').textContent = 'AOI defined — ' + cctvAoiLayer.getLatLngs()[0].length + ' vertices';
                cctvIsDrawing = false;
                document.getElementById('cctvDrawAoiBtn').innerHTML = '<i class="fas fa-draw-polygon mr-1"></i> Redraw AOI';
                setCctvStatus('AOI drawn successfully', 'fas fa-check-circle', 3000);
            });

            // Fetch & render sites
            setCctvStatus('Fetching site data…', 'fas fa-database fa-beat-fade');
            try {
                let data;
                if (siteDataCache && siteDataCache.length > 0) {
                    data = siteDataCache;
                } else {
                    const params = new URLSearchParams();
                    const weekSel = document.getElementById('weekSelect');
                    const regionSel = document.getElementById('regionSelect');
                    if (weekSel && weekSel.value && weekSel.value !== 'All') params.set('week', weekSel.value);
                    if (regionSel && regionSel.value && regionSel.value !== 'All') params.set('region', regionSel.value);
                    const resp = await fetch('/api/sites?' + params.toString());
                    data = await resp.json();
                    if (!Array.isArray(data)) data = [];
                }
                cctvSiteDataCache = data.filter(s => s && s.site_id && !isNaN(parseFloat(s.lat)) && !isNaN(parseFloat(s.lng)) && (parseFloat(s.lat) !== 0 || parseFloat(s.lng) !== 0));
                cctvSiteDataCache.forEach(s => { s.lat = parseFloat(s.lat); s.lng = parseFloat(s.lng); });

                setCctvStatus('Rendering ' + cctvSiteDataCache.length + ' sites…', 'fas fa-broadcast-tower fa-beat-fade');
                await new Promise(r => setTimeout(r, 50));

                cctvSiteMarkers = L.markerClusterGroup({ disableClusteringAtZoom: 14, maxClusterRadius: 50, iconCreateFunction: function(c) { return new L.DivIcon({ html: '<div class="cluster-base cluster-normal"><span>' + c.getChildCount() + '</span></div>', className: 'marker-cluster', iconSize: new L.Point(40, 40) }); } });
                cctvSiteDataCache.forEach(site => {
                    const icon = L.divIcon({ className: 'custom-pin', html: '<div style="background:#2563eb; width:12px; height:12px; border-radius:50%; border:2px solid white; box-shadow:0 2px 5px rgba(0,0,0,0.3);"></div>', iconSize: [12, 12] });
                    const popup = '<div style="padding:8px; min-width:180px;"><div style="font-weight:800; color:#1e3a8a;">' + site.site_id + '</div><div style="font-size:0.75rem; color:#64748b;">' + (site.site_name||'') + '</div></div>';
                    cctvSiteMarkers.addLayer(L.marker([site.lat, site.lng], { icon }).bindPopup(popup));

                    // Draw coverage
                    if (site.coverage) {
                        const azGroups = {};
                        site.coverage.forEach(sec => { const a = parseFloat(sec.az); if (!azGroups[a]) azGroups[a] = {}; azGroups[a][sec.tech] = sec; });
                        Object.entries(azGroups).forEach(([azStr, techs]) => {
                            const az = parseFloat(azStr);
                            const maxRad = Math.max(...Object.values(techs).map(t => t.rad || 1000));
                            const cfg = [{ tech:'5G',i:0,o:0.25,c:'#eab308'},{tech:'4G',i:0.25,o:0.50,c:'#3b82f6'},{tech:'3G',i:0.50,o:0.75,c:'#f97316'},{tech:'2G',i:0.75,o:1.0,c:'#6b7280'}];
                            cfg.forEach(x => {
                                if (techs[x.tech]) {
                                    const bw = techs[x.tech].bw || 65;
                                    const pts = cctvAnnulusSector(site.lat, site.lng, az, maxRad*x.i, maxRad*x.o, bw);
                                    L.polygon(pts, { color: x.c, weight: 1, fillOpacity: 0.3 }).addTo(cctvCoverageGroup);
                                }
                            });
                        });
                    }
                });
                cctvMap.addLayer(cctvSiteMarkers);

                if (cctvSiteDataCache.length > 0) {
                    const lats = cctvSiteDataCache.map(s => s.lat), lngs = cctvSiteDataCache.map(s => s.lng);
                    cctvMap.fitBounds([[Math.min(...lats), Math.min(...lngs)], [Math.max(...lats), Math.max(...lngs)]], { padding: [40, 40] });
                }
                setCctvStatus(cctvSiteDataCache.length + ' sites loaded', 'fas fa-check-circle', 4000);
            } catch (err) {
                console.error('CCTV map init error:', err);
                setCctvStatus('Failed to load sites: ' + err.message, 'fas fa-times-circle');
            }
        }

        function cctvAnnulusSector(lat, lng, azimuth, innerR, outerR, bw) {
            const R = 6378137, startA = (azimuth - bw/2) * Math.PI/180, endA = (azimuth + bw/2) * Math.PI/180;
            const latR = lat * Math.PI/180, lngR = lng * Math.PI/180, pts = [], steps = 24;
            for (let i = 0; i <= steps; i++) { const a = startA + (endA-startA)*(i/steps); pts.push([(latR + (outerR/R)*Math.cos(a))*180/Math.PI, (lngR + (outerR/R)*Math.sin(a)/Math.cos(latR))*180/Math.PI]); }
            for (let i = steps; i >= 0; i--) { const a = startA + (endA-startA)*(i/steps); pts.push([(latR + (innerR/R)*Math.cos(a))*180/Math.PI, (lngR + (innerR/R)*Math.sin(a)/Math.cos(latR))*180/Math.PI]); }
            return pts;
        }

        // AOI is now derived from uploaded building + parking GeoJSON
        function cctvStartDrawAOI() { /* no longer used */ }

        // ── CCTV Pipeline — sends files to backend /api/cctv/run which runs cctv2.py via PyQGIS ──
        async function cctvRunPipeline() {
            const missing = [];
            if (!cctvInputs.building.geojson) missing.push('Building');
            if (!cctvInputs.parking.geojson) missing.push('Parking Area');
            if (!cctvInputs.poles.geojson) missing.push('Pole Points');
            if (!cctvInputs.camera.csv) missing.push('Camera Table');
            if (!cctvInputs.offset.csv) missing.push('Offset Table');
            if (missing.length > 0) { alert('Missing inputs: ' + missing.join(', ')); return; }
            if (!cctvMap) return;

            const runBtn = document.getElementById('cctvRunBtn');
            runBtn.disabled = true;
            runBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin mr-1"></i> Processing…';
            cctvResultLayers.clearLayers();

            try {
                setCctvStatus('Uploading files to QGIS server…', 'fas fa-upload fa-beat-fade');

                const formData = new FormData();
                // GeoJSON inputs
                for (const [key, inputKey] of [['building','building'],['parking_area','parking'],['pole_points','poles']]) {
                    const blob = new Blob([JSON.stringify(cctvInputs[inputKey].geojson)], { type: 'application/json' });
                    formData.append(key, blob, key + '.geojson');
                }
                // CSV inputs
                formData.append('camera_table', new Blob([cctvInputs.camera.csv], { type: 'text/csv' }), 'camera_table.csv');
                formData.append('offset_table', new Blob([cctvInputs.offset.csv], { type: 'text/csv' }), 'offset_table.csv');

                setCctvStatus('Running QGIS cctv2 pipeline (may take a minute)…', 'fas fa-cogs fa-beat-fade');

                const resp = await fetch('/api/cctv/run', { method: 'POST', body: formData });

                let data;
                try {
                    data = await resp.json();
                } catch (parseErr) {
                    throw new Error('Server returned ' + resp.status + ' (' + resp.statusText + '). The QGIS processing server may not be available.');
                }

                if (!resp.ok || data.error) {
                    const detail = data.detail ? '\n\nDetail: ' + data.detail.slice(-500) : '';
                    throw new Error((data.error || 'Server error ' + resp.status) + detail);
                }

                setCctvStatus('Rendering QGIS output layers…', 'fas fa-paint-brush fa-beat-fade');
                await new Promise(r => setTimeout(r, 100));

                const layers = data.layers || {};
                const layerStyles = {
                    'aoi':                { color: '#22c55e', weight: 2, fillOpacity: 0.08, dashArray: '6,4' },
                    'surv_area':          { color: '#0ea5e9', weight: 1.5, fillOpacity: 0.06, dashArray: '4,4' },
                    'hex_grid':           { color: '#a855f7', weight: 0.6, fillOpacity: 0.04, dashArray: '3,3' },
                    'dissolved_buildings': { color: '#6366f1', weight: 1.5, fillOpacity: 0.12 },
                    'candidate_cctv':     { pt: { radius: 4, color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.8, weight: 1 } },
                    'poles':              { pt: { radius: 4, color: '#f59e0b', fillColor: '#f59e0b', fillOpacity: 0.8, weight: 1 } },
                    'cand_cctv_clean':    { pt: { radius: 5, color: '#dc2626', fillColor: '#dc2626', fillOpacity: 0.9, weight: 1.5 } },
                    'wedge':              { color: '#3b82f6', weight: 1, fillColor: '#3b82f6', fillOpacity: 0.18 },
                    'camera_cost_summary': null
                };

                let totalBounds = null;
                for (const [name, geojson] of Object.entries(layers)) {
                    if (!geojson || !geojson.features || !geojson.features.length || name === 'camera_cost_summary') continue;
                    const st = layerStyles[name] || { color: '#64748b', weight: 1, fillOpacity: 0.1 };
                    const opts = {
                        onEachFeature: (f, layer) => {
                            const p = f.properties || {};
                            let html = '<div style="font-size:0.78rem;"><b>' + name.replace(/_/g,' ').toUpperCase() + '</b><br>';
                            for (const [k,v] of Object.entries(p)) { if (v != null && v !== '') html += '<b>' + k.trim() + ':</b> ' + v + '<br>'; }
                            layer.bindPopup(html + '</div>');
                        }
                    };
                    if (st && st.pt) opts.pointToLayer = (f, ll) => L.circleMarker(ll, st.pt);
                    else if (st) opts.style = st;
                    const lyr = L.geoJSON(geojson, opts);
                    cctvResultLayers.addLayer(lyr);
                    const b = lyr.getBounds();
                    if (b && b.isValid()) totalBounds = totalBounds ? totalBounds.extend(b) : b;
                }
                if (totalBounds) cctvMap.fitBounds(totalBounds, { padding: [60, 60] });

                // Cost summary
                let html = '<div style="display:grid; grid-template-columns:1fr 1fr; gap:6px;">';
                if (layers.cand_cctv_clean) html += '<div><span style="font-weight:800; font-size:1rem;">' + (layers.cand_cctv_clean.features||[]).length + '</span><br><span style="font-size:0.65rem;">CCTV Candidates</span></div>';
                if (layers.wedge) html += '<div><span style="font-weight:800; color:#3b82f6; font-size:1rem;">' + (layers.wedge.features||[]).length + '</span><br><span style="font-size:0.65rem;">FOV Wedges</span></div>';
                if (layers.hex_grid) html += '<div><span style="font-weight:800; color:#a855f7;">' + (layers.hex_grid.features||[]).length + '</span><br><span style="font-size:0.65rem;">Hex Cells</span></div>';
                if (layers.poles) html += '<div><span style="font-weight:800; color:#f59e0b;">' + (layers.poles.features||[]).length + '</span><br><span style="font-size:0.65rem;">Poles</span></div>';
                html += '</div>';

                const cs = layers.camera_cost_summary;
                if (cs && cs.features && cs.features.length) {
                    html += '<div style="margin-top:10px; font-weight:700; font-size:0.72rem; color:#92400e;">Cost Breakdown (QGIS):</div>';
                    let total = 0;
                    cs.features.forEach(f => {
                        const p = f.properties || {};
                        const t = (p.camera_type || p['camera_type\n'] || '?').trim();
                        const c = p.count || p['count\n'] || 0;
                        const u = p.unit_price_rm || p['unit_price_rm\n'] || 0;
                        const tc = p.total_cost_rm || p['total_cost_rm\n'] || 0;
                        total += tc;
                        html += '<div style="font-size:0.7rem; padding:4px 0; border-bottom:1px solid #fde68a;">' + t + ': ' + c + ' × RM ' + Number(u).toLocaleString() + ' = <b>RM ' + Number(tc).toLocaleString() + '</b></div>';
                    });
                    html += '<div style="font-size:0.82rem; font-weight:800; color:#dc2626; margin-top:6px;">Total: RM ' + total.toLocaleString() + '</div>';
                }

                document.getElementById('cctvResultsPanel').style.display = 'block';
                document.getElementById('cctvResultsContent').innerHTML = html;

                const tot = Object.values(layers).reduce((s,l) => s + ((l && l.features) ? l.features.length : 0), 0);
                setCctvStatus('QGIS pipeline complete — ' + tot + ' features', 'fas fa-check-circle', 6000);

            } catch (err) {
                console.error('CCTV pipeline error:', err);
                setCctvStatus('Pipeline error: ' + err.message, 'fas fa-times-circle');
                alert('CCTV Pipeline failed:\n' + err.message);
            } finally {
                runBtn.disabled = false;
                runBtn.innerHTML = '<i class="fas fa-play mr-1"></i> Run CCTV Pipeline';
            }
        }

        function cctvClearResults() {
            cctvResultLayers.clearLayers();
            document.getElementById('cctvResultsPanel').style.display = 'none';
            setCctvStatus('Results cleared', 'fas fa-check-circle', 2000);
        }

        // ── CCTV Search ──
        let cctvSearchTimeout = null;
        function onCctvSearch(query) {
            clearTimeout(cctvSearchTimeout);
            const resultsDiv = document.getElementById('cctvSearchResults');
            if (!query || query.length < 2) { resultsDiv.style.display = 'none'; return; }
            cctvSearchTimeout = setTimeout(() => {
                const q = query.toUpperCase();
                const matches = cctvSiteDataCache.filter(s => (s.site_id||'').toUpperCase().includes(q) || (s.site_name||'').toUpperCase().includes(q)).slice(0, 15);
                if (!matches.length) { resultsDiv.innerHTML = '<div style="padding:12px 16px; color:#94a3b8; font-size:0.82rem;">No sites found</div>'; resultsDiv.style.display = 'block'; return; }
                resultsDiv.innerHTML = matches.map(s =>
                    `<div onclick="flyToCctvSite('${s.site_id}')" style="padding:8px 12px; cursor:pointer; display:flex; align-items:center; gap:8px; border-bottom:1px solid #f1f5f9;" onmouseover="this.style.background='#f0f9ff'" onmouseout="this.style.background='white'">
                        <i class="fas fa-broadcast-tower" style="color:#2563eb; font-size:0.8rem;"></i>
                        <div style="flex:1;"><div style="font-weight:700; font-size:0.8rem; color:#1e293b;">${s.site_id}</div><div style="font-size:0.7rem; color:#64748b;">${s.site_name||''}</div></div>
                    </div>`).join('');
                resultsDiv.style.display = 'block';
            }, 150);
        }
        function flyToCctvSite(siteId) {
            document.getElementById('cctvSearchResults').style.display = 'none';
            const site = cctvSiteDataCache.find(s => s.site_id === siteId);
            if (!site || !cctvMap) return;
            document.getElementById('cctvSearchInput').value = site.site_id + ' — ' + (site.site_name || '');
            cctvMap.flyTo([site.lat, site.lng], 16, { duration: 1 });
        }
        document.addEventListener('click', function(e) {
            const sb = document.getElementById('cctvSearchInput');
            const rd = document.getElementById('cctvSearchResults');
            if (sb && rd && !sb.contains(e.target) && !rd.contains(e.target)) rd.style.display = 'none';
        });

        // ==========================================
        // METABASE DASHBOARD EMBEDDING
        // ==========================================

        const METABASE_DASHBOARD_ID = 2;

        async function openMetabaseDashboard() {
            document.getElementById('metabaseModal').classList.remove('hidden');
            const container = document.getElementById('metabaseEmbedContainer');

            container.innerHTML = '<div class="flex items-center justify-center h-full text-gray-500"><i class="fas fa-circle-notch fa-spin text-3xl mr-3"></i> Loading Analytics Engine...</div>';

            try {
                const res = await fetch(`/api/dashboard/embed?dashboard_id=${METABASE_DASHBOARD_ID}`);
                const data = await res.json();

                if (!res.ok || !data.iframeUrl) {
                    throw new Error(data.error || "Failed to fetch Metabase URL");
                }

                container.innerHTML = `<iframe src="${data.iframeUrl}" frameborder="0" width="100%" height="100%" allowtransparency></iframe>`;

            } catch (err) {
                console.error("Dashboard Embed Error:", err);
                container.innerHTML = `<div class="flex items-center justify-center h-full text-red-500 font-bold"><i class="fas fa-exclamation-triangle mr-2"></i> Error loading dashboard: ${err.message}</div>`;
            }
        }

        function closeMetabaseDashboard() {
            document.getElementById('metabaseModal').classList.add('hidden');
            document.getElementById('metabaseEmbedContainer').innerHTML = '';
        }

        // ==========================================
        // CESIUM 3D MAP INITIALIZATION
        // ==========================================
        let cesiumViewer = null;
        let cesiumInitialized = false;

        function setCesiumStatus(msg, icon, autohide) {
            const el = document.getElementById('cesium3dStatus');
            const txt = document.getElementById('cesiumStatusText');
            const spin = document.getElementById('cesiumStatusSpinner');
            if (!el) return;
            txt.textContent = msg;
            spin.className = icon || 'fas fa-circle-notch fa-spin';
            el.style.opacity = '1';
            el.style.pointerEvents = 'none';
            if (autohide) {
                setTimeout(() => { el.style.opacity = '0'; }, autohide);
            }
        }

        async function initCesiumIfNeeded() {
            if (cesiumInitialized) {
                if (cesiumViewer) cesiumViewer.resize();
                return;
            }
            cesiumInitialized = true;

            setCesiumStatus('Initializing 3D viewer…', 'fas fa-circle-notch fa-spin');

            Cesium.Ion.defaultAccessToken = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJjMjg4MzRjNi01ZTRlLTQ4NzgtOGVlNi0yMjFhMjMyODQ2NjAiLCJpZCI6NDAzNTkyLCJpYXQiOjE3NzQ3NjQwNTN9.Yw-hl_nLMDwpYDeB_-w1xT6bj1vBpbCEIKCePeo6ehU";

            try {
                setCesiumStatus('Loading terrain data…', 'fas fa-mountain fa-beat-fade');
                cesiumViewer = new Cesium.Viewer('cesiumContainer', {
                    terrainProvider: await Cesium.createWorldTerrainAsync(),
                    timeline: false,
                    animation: false
                });

                cesiumViewer.scene.globe.enableLighting = true;

                setCesiumStatus('Loading 3D buildings…', 'fas fa-building fa-beat-fade');
                const buildings = await Cesium.createOsmBuildingsAsync();
                cesiumViewer.scene.primitives.add(buildings);

                // Only fly to the default position if no pegman drop is pending
                if (!window._pegmanPendingDrop) {
                    cesiumViewer.camera.flyTo({
                        destination: Cesium.Cartesian3.fromDegrees(101.6869, 3.1390, 1500)
                    });
                }

                setCesiumStatus('3D viewer ready', 'fas fa-check-circle', 3000);
                // Show the "Load Cell Sites" button
                document.getElementById('cesiumLoadSitesBtn').style.display = 'flex';

            } catch (e) {
                console.error('Cesium init error:', e);
                setCesiumStatus('Failed to load 3D Map: ' + e.message, 'fas fa-times-circle');
                document.getElementById('cesiumContainer').innerHTML =
                    '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#dc2626;font-weight:bold;font-size:1.1rem;"><i class="fas fa-exclamation-triangle" style="margin-right:8px;"></i> Failed to load 3D Map: ' + e.message + '</div>';
            }
        }

        // ==========================================
        // LOAD / UNLOAD 3D CELL SITES (USER-TRIGGERED)
        // ==========================================
        let cesiumSitesLoaded = false;

        async function toggleCesiumSites() {
            const btn = document.getElementById('cesiumLoadSitesBtn');
            const searchBar = document.getElementById('cesiumSearchBar');

            if (cesiumSitesLoaded) {
                // Unload sites
                cesiumViewer.entities.removeAll();
                cesiumSitesLoaded = false;
                btn.innerHTML = '<i class="fas fa-broadcast-tower"></i> Load Cell Sites';
                btn.style.background = '#1d4ed8';
                searchBar.style.display = 'none';
                setCesiumStatus('Cell sites removed', 'fas fa-check-circle', 2500);
                return;
            }

            // Load sites
            btn.disabled = true;
            btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Loading…';
            btn.style.opacity = '0.7';

            // Fetch data if needed
            if (!siteDataCache || siteDataCache.length === 0) {
                setCesiumStatus('Fetching site data from server…', 'fas fa-database fa-beat-fade');
                try {
                    const params = new URLSearchParams();
                    const weekSel = document.getElementById('weekSelect');
                    const regionSel = document.getElementById('regionSelect');
                    if (weekSel && weekSel.value && weekSel.value !== 'All') params.set('week', weekSel.value);
                    if (regionSel && regionSel.value && regionSel.value !== 'All') params.set('region', regionSel.value);
                    const resp = await fetch('/api/sites?' + params.toString());
                    const data = await resp.json();
                    if (Array.isArray(data)) {
                        siteDataCache = data.filter(s => {
                            if (!s || !s.site_id) return false;
                            const lat = parseFloat(s.lat); const lng = parseFloat(s.lng);
                            return !isNaN(lat) && !isNaN(lng) && (lat !== 0 || lng !== 0);
                        });
                    }
                } catch (fetchErr) {
                    console.warn('3D map: could not fetch site data:', fetchErr);
                    setCesiumStatus('Failed to fetch site data: ' + fetchErr.message, 'fas fa-times-circle', 5000);
                    btn.disabled = false;
                    btn.innerHTML = '<i class="fas fa-broadcast-tower"></i> Load Cell Sites';
                    btn.style.opacity = '1';
                    return;
                }
            }

            if (!siteDataCache || siteDataCache.length === 0) {
                setCesiumStatus('No site data available — try loading data from the Coverage Map tab first', 'fas fa-exclamation-triangle', 6000);
                btn.disabled = false;
                btn.innerHTML = '<i class="fas fa-broadcast-tower"></i> Load Cell Sites';
                btn.style.opacity = '1';
                return;
            }

            setCesiumStatus('Building ' + siteDataCache.length + ' cell towers…', 'fas fa-broadcast-tower fa-beat-fade');
            await new Promise(r => setTimeout(r, 100));

            add3dSiteMarkers();
            cesiumSitesLoaded = true;

            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-eye-slash"></i> Unload Cell Sites';
            btn.style.background = '#dc2626';
            btn.style.opacity = '1';
            searchBar.style.display = '';
            setCesiumStatus(siteDataCache.length + ' sites loaded successfully', 'fas fa-check-circle', 4000);
        }

        // ==========================================
        // 3D MAP SEARCH
        // ==========================================
        let cesiumSearchTimeout = null;

        function onCesiumSearch(query) {
            clearTimeout(cesiumSearchTimeout);
            const resultsDiv = document.getElementById('cesiumSearchResults');
            if (!query || query.length < 2) {
                resultsDiv.style.display = 'none';
                return;
            }
            cesiumSearchTimeout = setTimeout(() => {
                const q = query.toUpperCase();
                const matches = (siteDataCache || []).filter(s => {
                    const id = (s.site_id || '').toUpperCase();
                    const name = (s.site_name || '').toUpperCase();
                    return id.includes(q) || name.includes(q);
                }).slice(0, 15);

                if (matches.length === 0) {
                    resultsDiv.innerHTML = '<div style="padding:12px 16px; color:#94a3b8; font-size:0.82rem;">No sites found</div>';
                    resultsDiv.style.display = 'block';
                    return;
                }

                resultsDiv.innerHTML = matches.map(s => {
                    const isCong = isSiteCongested(s);
                    return `<div onclick="flyToSite3d('${s.site_id}')" style="padding:10px 16px; cursor:pointer; display:flex; align-items:center; gap:10px; border-bottom:1px solid #f1f5f9; transition:background 0.15s;"
                        onmouseover="this.style.background='#f0f9ff'" onmouseout="this.style.background='white'">
                        <i class="fas fa-broadcast-tower" style="color:${isCong ? '#dc2626' : '#2563eb'}; font-size:0.9rem; width:18px; text-align:center;"></i>
                        <div style="flex:1; min-width:0;">
                            <div style="font-weight:700; font-size:0.82rem; color:#1e293b;">${s.site_id}</div>
                            <div style="font-size:0.72rem; color:#64748b; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${s.site_name || 'Unnamed'}</div>
                        </div>
                        ${isCong ? '<span style="background:#fee2e2; color:#dc2626; font-size:0.6rem; font-weight:800; padding:2px 6px; border-radius:4px;">CONGESTED</span>' : ''}
                    </div>`;
                }).join('');
                resultsDiv.style.display = 'block';
            }, 150);
        }

        function isSiteCongested(site) {
            if (!site.sectors) return false;
            const areaStr = String(site.area_target || '').toLowerCase();
            const isUrban = areaStr.includes('urban') || areaStr.includes('kmc');
            const prbThreshold = isUrban ? 80.0 : 92.0;
            for (let sec of site.sectors) {
                if (parseFloat(sec.prb ?? 0) >= prbThreshold) return true;
            }
            return false;
        }

        function flyToSite3d(siteId) {
            const resultsDiv = document.getElementById('cesiumSearchResults');
            const input = document.getElementById('cesiumSearchInput');
            resultsDiv.style.display = 'none';

            const site = (siteDataCache || []).find(s => s.site_id === siteId);
            if (!site || !cesiumViewer) return;

            input.value = site.site_id + ' — ' + (site.site_name || '');

            cesiumViewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(
                    parseFloat(site.lng),
                    parseFloat(site.lat),
                    400
                ),
                orientation: {
                    heading: Cesium.Math.toRadians(0),
                    pitch: Cesium.Math.toRadians(-45),
                    roll: 0
                },
                duration: 1.5
            });
        }

        // Close search dropdown when clicking elsewhere
        document.addEventListener('click', function(e) {
            const searchBox = document.getElementById('cesiumSearchInput');
            const resultsDiv = document.getElementById('cesiumSearchResults');
            if (searchBox && resultsDiv && !searchBox.contains(e.target) && !resultsDiv.contains(e.target)) {
                resultsDiv.style.display = 'none';
            }
        });

        // ==========================================
        // PEGMAN DRAG-TO-3D (from 2D map)
        // ==========================================
        (function() {
            const pegmanBtn      = document.getElementById('pegmanBtn');
            const pegmanDock     = document.getElementById('pegmanDock');
            const pegmanDockLabel = document.getElementById('pegmanDockLabel');
            const ghost          = document.getElementById('pegmanGhost');
            const dropOverlay    = document.getElementById('pegmanDropOverlay');
            const tooltip        = document.getElementById('pegmanTooltip');

            let dragging  = false;
            let overDock  = false; // whether ghost is hovering over the home dock
            let mapDiv    = null;

            function getMapDiv() {
                return document.getElementById('map');
            }

            // Highlight dock as "return home" zone during drag
            function setDockReturnState(active) {
                if (active) {
                    pegmanDock.style.borderColor   = '#ef4444';
                    pegmanDock.style.background    = 'linear-gradient(160deg,#fef2f2 0%,#fee2e2 100%)';
                    pegmanDock.style.boxShadow     = '0 0 0 3px rgba(239,68,68,0.25), 0 2px 8px rgba(0,0,0,0.13)';
                    pegmanDockLabel.textContent    = '✕ Cancel';
                    pegmanDockLabel.style.color    = '#ef4444';
                } else {
                    pegmanDock.style.borderColor   = '#cbd5e1';
                    pegmanDock.style.background    = 'linear-gradient(160deg,#f8fafc 0%,#e2e8f0 100%)';
                    pegmanDock.style.boxShadow     = '0 2px 8px rgba(0,0,0,0.13), inset 0 1px 0 rgba(255,255,255,0.8)';
                    pegmanDockLabel.textContent    = '3D View';
                    pegmanDockLabel.style.color    = '#94a3b8';
                }
            }

            // Reset dock to idle state after drag ends
            function resetDock() {
                pegmanBtn.style.opacity   = '1';
                pegmanBtn.style.transform = '';
                pegmanBtn.style.filter    = '';
                setDockReturnState(false);
                overDock = false;
            }

            function startDrag(e) {
                e.preventDefault();
                dragging = true;
                mapDiv   = getMapDiv();

                // Size the overlay to exactly match the live map div bounds
                if (mapDiv) {
                    const r = mapDiv.getBoundingClientRect();
                    dropOverlay.style.left   = r.left   + 'px';
                    dropOverlay.style.top    = r.top    + 'px';
                    dropOverlay.style.width  = r.width  + 'px';
                    dropOverlay.style.height = r.height + 'px';
                }

                ghost.style.display       = 'block';
                dropOverlay.style.display = 'block';
                tooltip.style.opacity     = '0';

                // Fade out pegman in dock to show the "slot"
                pegmanBtn.style.opacity   = '0.18';
                pegmanBtn.style.transform = 'rotate(-12deg) scale(0.85)';
                pegmanBtn.style.filter    = 'drop-shadow(0 8px 14px rgba(0,0,0,0.5))';

                moveGhost(e);
            }

            function moveGhost(e) {
                const clientX = e.touches ? e.touches[0].clientX : e.clientX;
                const clientY = e.touches ? e.touches[0].clientY : e.clientY;
                ghost.style.left = clientX + 'px';
                ghost.style.top  = clientY + 'px';

                // Check if cursor is hovering over dock (return zone)
                if (dragging && pegmanDock) {
                    const dockRect = pegmanDock.getBoundingClientRect();
                    const isOver = clientX >= dockRect.left && clientX <= dockRect.right &&
                                   clientY >= dockRect.top  && clientY <= dockRect.bottom;
                    if (isOver !== overDock) {
                        overDock = isOver;
                        setDockReturnState(isOver);
                    }
                }
            }

            function onMove(e) {
                if (!dragging) return;
                moveGhost(e);
            }

            function onDrop(e) {
                if (!dragging) return;
                dragging = false;

                ghost.style.display       = 'none';
                dropOverlay.style.display = 'none';

                const clientX = e.changedTouches ? e.changedTouches[0].clientX : e.clientX;
                const clientY = e.changedTouches ? e.changedTouches[0].clientY : e.clientY;

                // If dropped back onto the dock → cancel (return home)
                if (pegmanDock) {
                    const dockRect = pegmanDock.getBoundingClientRect();
                    const droppedOnDock = clientX >= dockRect.left && clientX <= dockRect.right &&
                                         clientY >= dockRect.top  && clientY <= dockRect.bottom;
                    if (droppedOnDock) {
                        resetDock();
                        // Bounce-back animation on the pegman
                        pegmanBtn.style.transition = 'transform 0.35s cubic-bezier(0.34,1.56,0.64,1), opacity 0.25s, filter 0.2s';
                        pegmanBtn.style.transform  = 'scale(1.15)';
                        pegmanBtn.style.opacity    = '1';
                        setTimeout(() => {
                            pegmanBtn.style.transform  = '';
                            pegmanBtn.style.transition = 'transform 0.2s, filter 0.2s, opacity 0.2s';
                        }, 350);
                        return;
                    }
                }

                resetDock();

                // Check if dropped inside the Leaflet map area
                const mDiv = getMapDiv();
                if (!mDiv || !window.map) return;

                const rect = mDiv.getBoundingClientRect();
                if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) return;

                // Convert screen coords → Leaflet LatLng
                const containerPoint = window.map.mouseEventToContainerPoint({ clientX, clientY });
                const latlng         = window.map.containerPointToLatLng(containerPoint);

                // Store pending destination so initCesiumIfNeeded skips the default flyTo
                window._pegmanPendingDrop = { lng: latlng.lng, lat: latlng.lat };

                // Switch to 3D tab and fly there
                switchMainTab('3d');

                // Wait for Cesium to initialise then fly
                function flyWhenReady() {
                    if (!cesiumViewer) {
                        setTimeout(flyWhenReady, 200);
                        return;
                    }
                    window._pegmanPendingDrop = null; // clear the pending flag
                    cesiumViewer.camera.flyTo({
                        destination : Cesium.Cartesian3.fromDegrees(latlng.lng, latlng.lat, 300),
                        orientation : {
                            heading : Cesium.Math.toRadians(0),
                            pitch   : Cesium.Math.toRadians(-35),
                            roll    : 0
                        },
                        duration : 1.8,
                        easingFunction: Cesium.EasingFunction.CUBIC_IN_OUT
                    });

                    // Drop marker flash
                    const pos = Cesium.Cartesian3.fromDegrees(latlng.lng, latlng.lat, 0);
                    const flash = cesiumViewer.entities.add({
                        position : pos,
                        point    : {
                            pixelSize       : 20,
                            color           : Cesium.Color.fromCssColorString('#f59e0b'),
                            outlineColor    : Cesium.Color.WHITE,
                            outlineWidth    : 3,
                            heightReference : Cesium.HeightReference.CLAMP_TO_GROUND,
                            scaleByDistance : new Cesium.NearFarScalar(10, 2.0, 3000, 0.5)
                        }
                    });
                    setTimeout(() => { try { cesiumViewer.entities.remove(flash); } catch(err){} }, 2500);
                }
                flyWhenReady();
            }

            function onCancel() {
                if (!dragging) return;
                dragging = false;
                ghost.style.display       = 'none';
                dropOverlay.style.display = 'none';
                resetDock();
            }

            // Mouse
            pegmanBtn.addEventListener('mousedown',  startDrag);
            document.addEventListener('mousemove',   onMove);
            document.addEventListener('mouseup',     onDrop);
            // Touch
            pegmanBtn.addEventListener('touchstart', startDrag, { passive: false });
            document.addEventListener('touchmove',   onMove,    { passive: false });
            document.addEventListener('touchend',    onDrop);
            // Escape cancels
            document.addEventListener('keydown', function(e) { if (e.key === 'Escape') onCancel(); });
        })();

        function add3dSiteMarkers() {
            if (!cesiumViewer || !siteDataCache) return;

            const DEFAULT_HEIGHT = 30;  // fallback if antenna_height is missing (meters)
            const BEAM_LENGTH     = 300; // meters (visual sector beam length)

            // Tech colours for sector beams
            const TECH_COLORS = {
                '5G': Cesium.Color.YELLOW.withAlpha(0.25),
                '4G': Cesium.Color.CORNFLOWERBLUE.withAlpha(0.20),
                '3G': Cesium.Color.ORANGE.withAlpha(0.18),
                '2G': Cesium.Color.GRAY.withAlpha(0.15)
            };

            siteDataCache.forEach(site => {
                if (!site.lat || !site.lng) return;
                const lng = parseFloat(site.lng);
                const lat = parseFloat(site.lat);

                // Use per-site antenna_height from Athena, fallback to default
                const towerH = (site.antenna_height && site.antenna_height > 0)
                    ? parseFloat(site.antenna_height)
                    : DEFAULT_HEIGHT;

                // --- Congestion check ---
                let isCongested = false;
                if (site.sectors) {
                    const areaStr = String(site.area_target || '').toLowerCase();
                    const isUrban = areaStr.includes('urban') || areaStr.includes('kmc');
                    const prbThreshold = isUrban ? 80.0 : 92.0;
                    for (let sec of site.sectors) {
                        const p = parseFloat(sec.prb ?? 0);
                        if (p >= prbThreshold) { isCongested = true; break; }
                    }
                }

                const ballColor = isCongested
                    ? Cesium.Color.fromCssColorString('#dc2626')
                    : Cesium.Color.fromCssColorString('#22c55e');

                // ====== BALL AT ANTENNA HEIGHT ======
                const BALL_RADIUS = 8; // meters
                cesiumViewer.entities.add({
                    position: Cesium.Cartesian3.fromDegrees(lng, lat, towerH),
                    ellipsoid: {
                        radii: new Cesium.Cartesian3(BALL_RADIUS, BALL_RADIUS, BALL_RADIUS),
                        material: ballColor,
                        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND
                    }
                });

                // ====== SECTOR BEAMS FROM COVERAGE DATA ======
                if (site.coverage && site.coverage.length > 0) {
                    const azGroups = {};
                    site.coverage.forEach(sec => {
                        const a = parseFloat(sec.az);
                        if (!azGroups[a]) azGroups[a] = [];
                        azGroups[a].push(sec);
                    });

                    Object.entries(azGroups).forEach(([azStr, sectors]) => {
                        const az = parseFloat(azStr);
                        sectors.forEach(sec => {
                            const tech    = sec.tech || '4G';
                            const beamColor = TECH_COLORS[tech] || TECH_COLORS['4G'];
                            const bw      = (sec.bw || 65) / 2;
                            const beamLen = Math.min(sec.rad || BEAM_LENGTH, 500);
                            // Beam fans out from the ball position
                            const beamTop    = towerH + 2;
                            const beamBottom = Math.max(towerH - 6, 0);

                            const beamPoints = [];
                            beamPoints.push(Cesium.Cartesian3.fromDegrees(lng, lat, beamTop));

                            const steps = 8;
                            for (let i = 0; i <= steps; i++) {
                                const angle    = az - bw + (2 * bw * i / steps);
                                const angleRad = Cesium.Math.toRadians(angle);
                                const endLng   = lng + (beamLen / 111320) * Math.sin(angleRad) / Math.cos(Cesium.Math.toRadians(lat));
                                const endLat   = lat + (beamLen / 111320) * Math.cos(angleRad);
                                beamPoints.push(Cesium.Cartesian3.fromDegrees(endLng, endLat, beamBottom));
                            }

                            cesiumViewer.entities.add({
                                polygon: {
                                    hierarchy: new Cesium.PolygonHierarchy(beamPoints),
                                    perPositionHeight: true,
                                    material: beamColor,
                                    outline: true,
                                    outlineColor: beamColor.withAlpha(0.4),
                                    outlineWidth: 1
                                }
                            });
                        });
                    });
                }

                // ====== SITE LABEL ======
                cesiumViewer.entities.add({
                    position: Cesium.Cartesian3.fromDegrees(lng, lat, towerH + 5),
                    label: {
                        text: site.site_id || '',
                        font: 'bold 12px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        outlineColor: isCongested
                            ? Cesium.Color.fromCssColorString('#991b1b')
                            : Cesium.Color.fromCssColorString('#1e3a8a'),
                        outlineWidth: 3,
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                        pixelOffset: new Cesium.Cartesian2(0, -8),
                        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 20000),
                        heightReference: Cesium.HeightReference.RELATIVE_TO_GROUND,
                        scaleByDistance: new Cesium.NearFarScalar(200, 1.0, 30000, 0.3),
                        showBackground: true,
                        backgroundColor: isCongested
                            ? Cesium.Color.fromCssColorString('#dc2626').withAlpha(0.8)
                            : Cesium.Color.fromCssColorString('#1d4ed8').withAlpha(0.8),
                        backgroundPadding: new Cesium.Cartesian2(6, 4)
                    },
                    description:
                        '<table style="width:100%;border-collapse:collapse;font-family:sans-serif;">' +
                        '<tr><td style="padding:6px;font-weight:bold;color:#1d4ed8;">Site ID</td><td style="padding:6px;">' + (site.site_id || '-') + '</td></tr>' +
                        '<tr><td style="padding:6px;font-weight:bold;color:#1d4ed8;">Name</td><td style="padding:6px;">' + (site.site_name || '-') + '</td></tr>' +
                        '<tr><td style="padding:6px;font-weight:bold;color:#1d4ed8;">Region</td><td style="padding:6px;">' + (site.region || '-') + '</td></tr>' +
                        '<tr><td style="padding:6px;font-weight:bold;color:#1d4ed8;">Antenna Height</td><td style="padding:6px;">' + towerH + ' m</td></tr>' +
                        '<tr><td style="padding:6px;font-weight:bold;color:#1d4ed8;">Status</td><td style="padding:6px;color:' + (isCongested ? '#dc2626;font-weight:bold' : '#059669') + ';">' + (isCongested ? 'CONGESTED' : 'Normal') + '</td></tr>' +
                        (site.sectors ? '<tr><td style="padding:6px;font-weight:bold;color:#1d4ed8;">Sectors</td><td style="padding:6px;">' + site.sectors.length + '</td></tr>' : '') +
                        '</table>'
                });
            });
        }


    // ================================================================
    // EOF (CCTV & Bitcoin) LAYER CONTROLS
    // ================================================================
    const _eofState = {
        cctv: { mode: 'satellite', dem: false, sat: null, labels: null, street: null, dem: null },
        mh:   { mode: 'satellite', dem: false, sat: null, labels: null, street: null, demLayer: null }
    };

    function _eofGetMap(key) { return key === 'cctv' ? (typeof cctvMap !== 'undefined' ? cctvMap : null) : (typeof mhMap !== 'undefined' ? mhMap : null); }

    function eofSetBaseMap(key, mode) {
        const m = _eofGetMap(key);
        if (!m) return;
        const s = _eofState[key];

        if (!s.sat) {
            s.sat    = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { attribution: '&copy; Esri', maxZoom: 19 });
            s.labels = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png', { attribution: '&copy; CartoDB', subdomains: 'abcd', maxZoom: 19 });
            s.street = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { attribution: '&copy; CartoDB', subdomains: 'abcd', maxZoom: 19 });
            s.demLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}', { attribution: '&copy; Esri', maxZoom: 19, opacity: 0.55, zIndex: 3 });
        }

        if (mode === 'satellite') {
            if (m.hasLayer(s.street)) m.removeLayer(s.street);
            s.sat.addTo(m); s.labels.addTo(m);
        } else {
            if (m.hasLayer(s.sat))    m.removeLayer(s.sat);
            if (m.hasLayer(s.labels)) m.removeLayer(s.labels);
            s.street.addTo(m);
        }
        s.mode = mode;
        if (s.demActive && s.demLayer) { s.demLayer.bringToFront(); if (mode === 'satellite') s.labels.bringToFront(); }

        // Update pill button active states
        const satBtn    = document.getElementById(key + '-btn-sat');
        const streetBtn = document.getElementById(key + '-btn-street');
        if (satBtn && streetBtn) {
            satBtn.style.background    = mode === 'satellite' ? '#0f2d52' : '#e2e8f0';
            satBtn.style.color         = mode === 'satellite' ? 'white'   : '#475569';
            streetBtn.style.background = mode === 'street'    ? '#0f2d52' : '#e2e8f0';
            streetBtn.style.color      = mode === 'street'    ? 'white'   : '#475569';
        }
    }

    function eofToggleDEM(key) {
        const m = _eofGetMap(key);
        if (!m) return;
        const s = _eofState[key];
        if (!s.sat) eofSetBaseMap(key, s.mode); // ensure layers created

        const demBtn = document.getElementById(key + '-btn-dem');
        if (s.demActive) {
            m.removeLayer(s.demLayer);
            s.demActive = false;
            if (demBtn) { demBtn.style.background = '#e2e8f0'; demBtn.style.color = '#475569'; }
        } else {
            s.demLayer.addTo(m); s.demLayer.bringToFront();
            if (s.mode === 'satellite' && s.labels) s.labels.bringToFront();
            s.demActive = true;
            if (demBtn) { demBtn.style.background = '#0f2d52'; demBtn.style.color = 'white'; }
        }
    }

    // Bootstrap EOF tile layers once each map is initialised
    (function waitForEofMaps() {
        let cctvReady = false, mhReady = false;
        function tryInit() {
            if (!cctvReady && typeof cctvMap !== 'undefined' && cctvMap) { eofSetBaseMap('cctv', 'satellite'); cctvReady = true; }
            if (!mhReady   && typeof mhMap   !== 'undefined' && mhMap)   { eofSetBaseMap('mh',   'satellite'); mhReady   = true; }
            if (!cctvReady || !mhReady) setTimeout(tryInit, 500);
        }
        setTimeout(tryInit, 800);
    })();

    // ================================================================
    // BASE MAP TOGGLE — Satellite ↔ Street
    // ================================================================
    // Split map tile layer refs (mirrored from left map)
    var splitSatLayer, splitLabelsLayer, splitStreetLayer, splitDemLayer;

    function _ensureSplitLayers() {
        if (!splitMap || splitSatLayer) return;
        splitSatLayer    = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { attribution: '&copy; Esri', maxZoom: 19 });
        splitLabelsLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png', { attribution: '&copy; CartoDB', subdomains: 'abcd', maxZoom: 19 });
        splitStreetLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { attribution: '&copy; CartoDB', subdomains: 'abcd', maxZoom: 19 });
        splitDemLayer    = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}', { attribution: '&copy; Esri', maxZoom: 19, opacity: 0.55, zIndex: 3 });
        // Start in same mode as current left map
        if (isSatelliteMode) { splitSatLayer.addTo(splitMap); splitLabelsLayer.addTo(splitMap); }
        else                 { splitStreetLayer.addTo(splitMap); }
        if (isDemActive) { splitDemLayer.addTo(splitMap); splitDemLayer.bringToFront(); if (isSatelliteMode) splitLabelsLayer.bringToFront(); }
    }

    function toggleBaseMap() {
        const sw     = document.getElementById('toggle-satellite');
        const icon   = document.getElementById('baseMapIcon');
        const label  = document.getElementById('baseMapLabel');

        if (isSatelliteMode) {
            map.removeLayer(satelliteLayer);
            map.removeLayer(labelsLayer);
            streetLayer.addTo(map);
            streetLayer.setZIndex(0);
            isSatelliteMode = false;
            sw.classList.remove('active');
            icon.style.background = 'linear-gradient(135deg,#2563eb,#7c3aed)';
            icon.innerHTML = '<i class="fas fa-map" style="color:white;font-size:0.7rem;"></i>';
            label.textContent = 'Street Map';
            // Mirror to both split maps
            if (splitActive) {
                _applyBaseMap('street', splitMap,       splitSatLayer, splitLabelsLayer, splitStreetLayer);
                _applyBaseMap('street', splitActualMap, null, null, null, true);
            }
        } else {
            map.removeLayer(streetLayer);
            satelliteLayer.addTo(map);
            labelsLayer.addTo(map);
            isSatelliteMode = true;
            sw.classList.add('active');
            icon.style.background = 'linear-gradient(135deg,#0f4c81,#1a7a4a)';
            icon.innerHTML = '<i class="fas fa-satellite" style="color:white;font-size:0.7rem;"></i>';
            label.textContent = 'Satellite View';
            // Mirror to both split maps
            if (splitActive) {
                _applyBaseMap('satellite', splitMap,       splitSatLayer, splitLabelsLayer, splitStreetLayer);
                _applyBaseMap('satellite', splitActualMap, null, null, null, true);
            }
        }
        if (isDemActive) {
            demLayer.bringToFront();
            if (splitActive) {
                if (splitDemLayer && splitMap)       { splitDemLayer.bringToFront(); if (isSatelliteMode) splitLabelsLayer.bringToFront(); }
                if (splitActualDemLayer && splitActualMap) { splitActualDemLayer.bringToFront(); }
            }
        }
    }

    // Helper — swap base tiles on a given map instance
    // For the actual-left map, we manage separate tile layer instances
    var splitActualSatLayer, splitActualLabelsLayer, splitActualStreetLayer, splitActualDemLayer;

    function _applyBaseMap(mode, targetMap, satL, labL, strL, isActual) {
        if (!targetMap) return;
        if (isActual) {
            // Lazy-create actual-side split layers
            if (!splitActualSatLayer) {
                splitActualSatLayer    = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom:19 });
                splitActualLabelsLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png', { subdomains:'abcd', maxZoom:19 });
                splitActualStreetLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { subdomains:'abcd', maxZoom:19 });
                splitActualDemLayer    = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}', { maxZoom:19, opacity:0.55, zIndex:3 });
            }
            satL = splitActualSatLayer; labL = splitActualLabelsLayer; strL = splitActualStreetLayer;
        }
        if (mode === 'street') {
            if (targetMap.hasLayer(satL)) targetMap.removeLayer(satL);
            if (targetMap.hasLayer(labL)) targetMap.removeLayer(labL);
            if (!targetMap.hasLayer(strL)) strL.addTo(targetMap);
        } else {
            if (targetMap.hasLayer(strL)) targetMap.removeLayer(strL);
            if (!targetMap.hasLayer(satL)) satL.addTo(targetMap);
            if (!targetMap.hasLayer(labL)) labL.addTo(targetMap);
        }
    }

    // ================================================================
    // DEM / HILLSHADE OVERLAY TOGGLE
    // ================================================================
    function toggleDemLayer() {
        const sw        = document.getElementById('toggle-dem');
        const opacityRow = document.getElementById('demOpacityRow');

        if (isDemActive) {
            map.removeLayer(demLayer);
            isDemActive = false;
            sw.classList.remove('active');
            opacityRow.style.display = 'none';
            if (splitActive) {
                if (splitDemLayer      && splitMap      && splitMap.hasLayer(splitDemLayer))       splitMap.removeLayer(splitDemLayer);
                if (splitActualDemLayer && splitActualMap && splitActualMap.hasLayer(splitActualDemLayer)) splitActualMap.removeLayer(splitActualDemLayer);
            }
        } else {
            demLayer.addTo(map);
            demLayer.bringToFront();
            if (isSatelliteMode && map.hasLayer(labelsLayer)) labelsLayer.bringToFront();
            isDemActive = true;
            sw.classList.add('active');
            opacityRow.style.display = 'flex';
            if (splitActive) {
                // Right (forecast) map
                _ensureSplitLayers();
                splitDemLayer.addTo(splitMap); splitDemLayer.bringToFront();
                if (isSatelliteMode && splitMap.hasLayer(splitLabelsLayer)) splitLabelsLayer.bringToFront();
                // Left (actual) map
                if (splitActualMap) {
                    _applyBaseMap(isSatelliteMode ? 'satellite' : 'street', splitActualMap, null, null, null, true);
                    splitActualDemLayer.addTo(splitActualMap); splitActualDemLayer.bringToFront();
                }
            }
        }
    }

    function setDemOpacity(val) {
        demLayer.setOpacity(val / 100);
        document.getElementById('demOpacityVal').textContent = val + '%';
        if (splitDemLayer)       splitDemLayer.setOpacity(val / 100);
        if (splitActualDemLayer) splitActualDemLayer.setOpacity(val / 100);
    }

    // ================================================================
    // GEO TOOLS — True North, Distance, Bearing & Azimuth
    // ================================================================

    let geoPickMode  = null;   // 'A' | 'B' | null
    let geoPointA    = null;
    let geoPointB    = null;
    let geoMarkerA   = null;
    let geoMarkerB   = null;
    let geoPolyline  = null;

    const _toRad = d => d * Math.PI / 180;
    const _toDeg = r => r * 180 / Math.PI;

    function haversineDistance(lat1, lon1, lat2, lon2) {
        const R = 6371000;
        const φ1 = _toRad(lat1), φ2 = _toRad(lat2);
        const Δφ = _toRad(lat2 - lat1), Δλ = _toRad(lon2 - lon1);
        const a = Math.sin(Δφ/2)**2 + Math.cos(φ1)*Math.cos(φ2)*Math.sin(Δλ/2)**2;
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function calculateBearing(lat1, lon1, lat2, lon2) {
        const φ1 = _toRad(lat1), φ2 = _toRad(lat2);
        const Δλ = _toRad(lon2 - lon1);
        const y = Math.sin(Δλ) * Math.cos(φ2);
        const x = Math.cos(φ1)*Math.sin(φ2) - Math.sin(φ1)*Math.cos(φ2)*Math.cos(Δλ);
        return (_toDeg(Math.atan2(y, x)) + 360) % 360;
    }

    function startGeoPickMode(point) {
        geoPickMode = point;
        const label = `Click map to set Point ${point}`;
        document.getElementById('geoPickStatusText').textContent = label;
        document.getElementById('geoPickStatus').classList.remove('hidden');
        if (map) map.getContainer().style.cursor = 'crosshair';
    }

    function _placeGeoMarker(lat, lng, point) {
        const isA = point === 'A';
        const color = isA ? '#2563eb' : '#dc2626';
        const fillColor = isA ? '#3b82f6' : '#ef4444';
        const ref = isA ? 'geoMarkerA' : 'geoMarkerB';
        if (window[ref]) map.removeLayer(window[ref]);
        window[ref] = L.circleMarker([lat, lng], {
            radius: 7, color, fillColor, fillOpacity: 1, weight: 2.5, interactive: false
        }).addTo(map);
        window[ref].bindTooltip(`<b>${point}</b>`, {
            permanent: true, direction: 'top', offset: [0, -8],
            className: 'leaflet-tooltip leaflet-tooltip-top',
            opacity: 1
        });
    }

    function setGeoPoint(lat, lng, point) {
        if (point === 'A') {
            geoPointA = {lat, lng};
            document.getElementById('geoLatA').value = lat.toFixed(6);
            document.getElementById('geoLngA').value = lng.toFixed(6);
        } else {
            geoPointB = {lat, lng};
            document.getElementById('geoLatB').value = lat.toFixed(6);
            document.getElementById('geoLngB').value = lng.toFixed(6);
        }
        _placeGeoMarker(lat, lng, point);

        geoPickMode = null;
        document.getElementById('geoPickStatus').classList.add('hidden');
        if (map) map.getContainer().style.cursor = '';
        runGeoCalc();
    }

    function runGeoCalc() {
        const latA = parseFloat(document.getElementById('geoLatA').value);
        const lngA = parseFloat(document.getElementById('geoLngA').value);
        const latB = parseFloat(document.getElementById('geoLatB').value);
        const lngB = parseFloat(document.getElementById('geoLngB').value);

        if (isNaN(latA) || isNaN(lngA) || isNaN(latB) || isNaN(lngB)) {
            document.getElementById('geoResults').classList.add('hidden');
            return;
        }

        const dist = haversineDistance(latA, lngA, latB, lngB);
        const bearing = calculateBearing(latA, lngA, latB, lngB);
        const backBearing = (bearing + 180) % 360;

        const distStr = dist >= 1000
            ? (dist / 1000).toFixed(3) + ' km'
            : dist.toFixed(1) + ' m';

        document.getElementById('geoDistResult').textContent        = distStr;
        document.getElementById('geoBearingResult').textContent     = bearing.toFixed(2) + '°';
        document.getElementById('geoBackBearingResult').textContent = backBearing.toFixed(2) + '°';
        document.getElementById('geoResults').classList.remove('hidden');

        // Draw dashed line between the two points
        if (geoPolyline) map.removeLayer(geoPolyline);
        geoPolyline = L.polyline([[latA, lngA], [latB, lngB]], {
            color: '#7c3aed', weight: 2, dashArray: '6 5', opacity: 0.85, interactive: false
        }).addTo(map);

        // Refresh markers with latest coordinate values
        _placeGeoMarker(latA, lngA, 'A');
        _placeGeoMarker(latB, lngB, 'B');
    }

    function clearGeoTools() {
        if (geoMarkerA)  { map.removeLayer(geoMarkerA);  geoMarkerA  = null; }
        if (geoMarkerB)  { map.removeLayer(geoMarkerB);  geoMarkerB  = null; }
        if (geoPolyline) { map.removeLayer(geoPolyline); geoPolyline = null; }
        geoPointA = geoPointB = null;
        ['geoLatA','geoLngA','geoLatB','geoLngB'].forEach(id => {
            document.getElementById(id).value = '';
        });
        document.getElementById('geoResults').classList.add('hidden');
        geoPickMode = null;
        document.getElementById('geoPickStatus').classList.add('hidden');
        if (map) map.getContainer().style.cursor = '';
    }

    // Attach geo-pick click handler on the map
    (function waitForMap() {
        if (typeof map !== 'undefined' && map) {
            map.on('click', function(e) {
                if (geoPickMode) setGeoPoint(e.latlng.lat, e.latlng.lng, geoPickMode);
            });
        } else {
            setTimeout(waitForMap, 300);
        }
    })();

    // ================================================================
    // GENSET — TNB Substation Finder
    // ================================================================
    let gensetMap          = null;
    let gensetMapInited    = false;
    let gensetRouteLines   = [];   // polylines for single-site result paths
    let gensetSitesData    = [];
    let gensetSiteMarkers  = {};
    let gensetSelectedSite = null;
    let gensetSelectedMarker = null;
    let gensetTnbMarkers   = [];
    let gensetRouteLine    = null;
    let gensetNearbyCircle = null;

    // ── Tile layers for the genset map ──
    let gsSatLayer, gsLabelsLayer, gsStreetLayer, gsDemLayer;
    let gsBaseMode  = 'satellite'; // 'satellite' | 'street'
    let gsDemActive = false;

    // ── Geo tools state (scoped to genset map) ──
    let gsGeoPickMode  = null;
    let gsGeoMarkerA   = null;
    let gsGeoMarkerB   = null;
    let gsGeoLine      = null;

    // ── Status badge helper ──
    function gsSetStatus(text, spin = true) {
        const badge = document.getElementById('gensetStatusBadge');
        const icon  = document.getElementById('gensetStatusIcon');
        const label = document.getElementById('gensetStatusText');
        if (!text) { badge.style.display = 'none'; return; }
        badge.style.display = 'flex';
        icon.className = spin ? 'fas fa-circle-notch fa-spin' : 'fas fa-check-circle';
        label.textContent = text;
    }

    const GENSET_SITE_ICON = (highlight) => L.divIcon({
        className: '',
        html: `<div style="width:12px;height:12px;border-radius:50%;background:${highlight ? '#f59e0b' : '#16a34a'};border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.35);"></div>`,
        iconSize: [12, 12], iconAnchor: [6, 6]
    });

    const GENSET_TNB_ICON = L.divIcon({
        className: '',
        html: `<div style="width:28px;height:28px;display:flex;align-items:center;justify-content:center;">
                 <div style="width:20px;height:20px;background:#f59e0b;border:2.5px solid white;border-radius:4px;box-shadow:0 2px 8px rgba(245,158,11,0.7);display:flex;align-items:center;justify-content:center;">
                   <i class="fas fa-bolt" style="color:white;font-size:9px;"></i>
                 </div>
               </div>`,
        iconSize: [28, 28], iconAnchor: [14, 14]
    });

    async function initGensetMap() {
        if (gensetMapInited) return;
        gensetMapInited = true;

        gensetMap = L.map('gensetMap', { zoomControl: false, preferCanvas: false })
                     .setView([4.2105, 101.9758], 6);

        // Zoom control top-right (same as Coverage Map), compass goes below it
        L.control.zoom({ position: 'topright' }).addTo(gensetMap);

        // Create a dedicated pane for the route so it always sits above tiles & DEM
        gensetMap.createPane('routePane');
        gensetMap.getPane('routePane').style.zIndex = 650; // above markerPane (600)
        gensetMap.getPane('routePane').style.pointerEvents = 'none';

        // Create a pane for the 2 km radius ring (below route, above tiles)
        gensetMap.createPane('ringPane');
        gensetMap.getPane('ringPane').style.zIndex = 420;
        gensetMap.getPane('ringPane').style.pointerEvents = 'none';

        // Tile layers
        gsSatLayer    = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            { attribution: 'Tiles &copy; Esri', maxZoom: 19 });
        gsLabelsLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png',
            { attribution: '&copy;OpenStreetMap,&copy;CartoDB', subdomains: 'abcd', maxZoom: 19 });
        gsStreetLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
            { attribution: '&copy;OpenStreetMap,&copy;CartoDB', subdomains: 'abcd', maxZoom: 19 });
        gsDemLayer    = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}',
            { attribution: 'Hillshade &copy; Esri, USGS, NOAA', maxZoom: 19, opacity: 0.55, zIndex: 3 });

        gsSatLayer.addTo(gensetMap);
        gsLabelsLayer.addTo(gensetMap);

        // Annotation drawing support on genset map
        drawnItemsGenset = new L.FeatureGroup().addTo(gensetMap);
        gensetMap.on(L.Draw.Event.CREATED, _onDrawCreated);

        // Attach geo-pick click handler
        gensetMap.on('click', function(e) {
            if (gsGeoPickMode) gsSetGeoPoint(e.latlng.lat, e.latlng.lng, gsGeoPickMode);
        });
        _attachCtxMenu(gensetMap);  // right-click → copy coordinates

        await gensetLoadSites();
    }

    // ── Layer controls ──
    function gsSetBaseMap(mode) {
        if (!gensetMap) return;
        if (mode === 'satellite') {
            if (gsStreetLayer) gensetMap.removeLayer(gsStreetLayer);
            gsSatLayer.addTo(gensetMap);
            gsLabelsLayer.addTo(gensetMap);
            gsBaseMode = 'satellite';
        } else {
            if (gsSatLayer)    gensetMap.removeLayer(gsSatLayer);
            if (gsLabelsLayer) gensetMap.removeLayer(gsLabelsLayer);
            gsStreetLayer.addTo(gensetMap);
            gsBaseMode = 'street';
        }
        if (gsDemActive) { gsDemLayer.bringToFront(); if (mode === 'satellite') gsLabelsLayer.bringToFront(); }

        // Update pill button states
        const satBtn    = document.getElementById('gs-btn-sat');
        const streetBtn = document.getElementById('gs-btn-street');
        if (satBtn) {
            satBtn.style.background    = mode === 'satellite' ? '#0f2d52' : '#e2e8f0';
            satBtn.style.color         = mode === 'satellite' ? 'white'   : '#475569';
        }
        if (streetBtn) {
            streetBtn.style.background = mode === 'street' ? '#0f2d52' : '#e2e8f0';
            streetBtn.style.color      = mode === 'street' ? 'white'   : '#475569';
        }
    }

    function gsToggleDEM() {
        if (!gensetMap) return;
        const demBtn = document.getElementById('gs-btn-dem');

        if (gsDemActive) {
            gensetMap.removeLayer(gsDemLayer);
            gsDemActive = false;
            if (demBtn) { demBtn.style.background = '#e2e8f0'; demBtn.style.color = '#475569'; }
        } else {
            gsDemLayer.addTo(gensetMap); gsDemLayer.bringToFront();
            if (gsBaseMode === 'satellite') gsLabelsLayer.bringToFront();
            gsDemActive = true;
            if (demBtn) { demBtn.style.background = '#0f2d52'; demBtn.style.color = 'white'; }
        }
    }

    function gsSetDemOpacity(val) {
        if (gsDemLayer) gsDemLayer.setOpacity(val / 100);
        document.getElementById('gsDemOpacityVal').textContent = val + '%';
    }

    // ── Sites loader ──
    async function gensetLoadSites() {
        gsSetStatus('Loading sites…');
        try {
            let raw;

            // Use the shared siteDataCache — same source as CCTV and Bitcoin tabs
            if (typeof siteDataCache !== 'undefined' && Array.isArray(siteDataCache) && siteDataCache.length > 0) {
                raw = siteDataCache;
            } else {
                // Cache miss — fetch from the same /api/sites endpoint and populate
                // the shared cache so Coverage Map / CCTV / Bitcoin also benefit
                const res = await fetch('/api/sites');
                raw = await res.json();
                if (!Array.isArray(raw)) raw = [];
                if (typeof siteDataCache !== 'undefined') siteDataCache = raw;
            }

            gensetSitesData = raw.filter(s => {
                const lat = parseFloat(s.lat), lng = parseFloat(s.lng);
                return s.site_id && !isNaN(lat) && !isNaN(lng) && (lat !== 0 || lng !== 0);
            });

            const clusterGroup = L.markerClusterGroup({
                maxClusterRadius: 50,
                iconCreateFunction: function(c) {
                    return new L.DivIcon({
                        html: '<div class="cluster-base cluster-normal"><span>' + c.getChildCount() + '</span></div>',
                        className: 'marker-cluster',
                        iconSize: new L.Point(40, 40)
                    });
                }
            });

            gensetSitesData.forEach(site => {
                site.lat = parseFloat(site.lat);
                site.lng = parseFloat(site.lng);
                const m = L.marker([site.lat, site.lng], { icon: GENSET_SITE_ICON(false) });
                m.on('click', () => gensetSelectSite(site, m));
                clusterGroup.addLayer(m);
                gensetSiteMarkers[site.site_id] = m;
            });

            gensetMap.addLayer(clusterGroup);
            gsSetStatus(gensetSitesData.length + ' sites loaded', false);
            setTimeout(() => gsSetStatus(null), 2500);
        } catch(e) {
            console.error('Genset: failed to load sites', e);
            gsSetStatus('Load failed', false);
        }
    }

    // ── Site selection ──
    function gensetSelectSite(site, marker) {
        if (gensetSelectedMarker) gensetSelectedMarker.setIcon(GENSET_SITE_ICON(false));
        gensetSelectedSite   = site;
        gensetSelectedMarker = marker;
        marker.setIcon(GENSET_SITE_ICON(true));
        gensetClearResults();
        document.getElementById('gensetSiteName').textContent = site.site_id + (site.site_name ? ' — ' + site.site_name : '');
        document.getElementById('gensetSiteDetails').innerHTML =
            `<span style="color:#6b7280;">Lat:</span> <b>${site.lat.toFixed(6)}</b>&emsp;` +
            `<span style="color:#6b7280;">Lng:</span> <b>${site.lng.toFixed(6)}</b><br>` +
            (site.region ? `<span style="color:#6b7280;">Region:</span> <b>${site.region}</b>` : '');
        document.getElementById('gensetSiteCard').style.display = 'block';
        document.getElementById('gensetResultPanel').style.display = 'none';
        gensetMap.flyTo([site.lat, site.lng], 14, { animate: true, duration: 1.2 });
    }

    // ── Single-site path selector ──────────────────────────────────────────
    window._gsActivePathIdx = 0;
    window._gsValidPaths    = [];
    function gsSelectPath(idx) {
        if (!window._gsValidPaths || !gensetRouteLines) return;
        window._gsActivePathIdx = idx;
        // Re-style all rows
        window._gsValidPaths.forEach((r, i) => {
            const row = document.getElementById('gsPathRow-'+i);
            if (row) row.style.background = i===idx ? '#fef3c7' : 'transparent';
        });
        // Re-style all polylines
        gensetRouteLines.forEach((line, i) => {
            line.setStyle({
                color:     i===idx ? '#f59e0b' : '#4ade80',
                weight:    i===idx ? 6 : 2.5,
                opacity:   i===idx ? 0.95 : 0.35,
                dashArray: i===idx ? null : '8 5',
            });
            if (i===idx) line.bringToFront();
        });
        // Pan to selected substation
        const r = window._gsValidPaths[idx];
        if (r && r.lat && r.lng) gensetMap.panTo([r.lat, r.lng]);
    }

    function gensetClearResults() {
        gensetTnbMarkers.forEach(m => gensetMap.removeLayer(m));
        gensetTnbMarkers = [];
        if (gensetRouteLine)    { gensetMap.removeLayer(gensetRouteLine);    gensetRouteLine    = null; }
        if (gensetNearbyCircle) { gensetMap.removeLayer(gensetNearbyCircle); gensetNearbyCircle = null; }
        document.getElementById('gensetFound').style.display    = 'none';
        document.getElementById('gensetNotFound').style.display = 'none';
        document.getElementById('gensetLoading').style.display  = 'none';
    }

    // ── TNB finder ──
    async function gensetFindTNB() {
        if (!gensetSelectedSite) return;
        const { lat, lng } = gensetSelectedSite;

        gensetClearResults();
        document.getElementById('gensetResultPanel').style.display = 'block';
        document.getElementById('gensetLoading').style.display = 'block';
        document.getElementById('gensetFindBtn').disabled = true;
        gsSetStatus('Querying Overpass…');

        gensetNearbyCircle = L.circle([lat, lng], {
            radius: 2000, color: '#16a34a', weight: 1.5,
            fillColor: '#16a34a', fillOpacity: 0.06, dashArray: '6,4',
            pane: 'ringPane'
        }).addTo(gensetMap);

        try {
            // ── Step 1: Overpass (browser-direct, same as original) ────────
            const query = `[out:json][timeout:25];
(
  node["power"="substation"](around:2000,${lat},${lng});
  way["power"="substation"](around:2000,${lat},${lng});
  relation["power"="substation"](around:2000,${lat},${lng});
);
out center;`;
            const ovData = await (await fetch('https://overpass-api.de/api/interpreter', { method: 'POST', body: query })).json();
            const substations = (ovData.elements || []).map(el => ({
                osm_id: String(el.id),
                name:   el.tags?.name || el.tags?.['name:en'] || el.tags?.operator || 'TNB Substation',
                lat:    el.type === 'node' ? el.lat  : el.center?.lat,
                lng:    el.type === 'node' ? el.lon  : el.center?.lon,
                tags:   el.tags || {}
            })).filter(s => s.lat && s.lng);

            if (substations.length === 0) { gensetShowNotFound(); return; }

            // Place all substation markers
            substations.forEach(sub => {
                const m = L.marker([sub.lat, sub.lng], { icon: GENSET_TNB_ICON })
                    .bindPopup(`<b style="color:#d97706;">${sub.name}</b><br>
                        <span style="font-size:0.75rem;color:#6b7280;">
                            ${sub.tags.voltage  ? 'Voltage: '  + sub.tags.voltage  + '<br>' : ''}
                            ${sub.tags.operator ? 'Operator: ' + sub.tags.operator + '<br>' : ''}
                            Lat: ${sub.lat.toFixed(6)}, Lng: ${sub.lng.toFixed(6)}
                        </span>`)
                    .addTo(gensetMap);
                gensetTnbMarkers.push(m);
            });

            // ── Step 2: OSMnx road routing via backend ─────────────────────
            gsSetStatus('Routing via road network…');
            const routeResp = await fetch('/api/genset/route', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lat, lng, substations })
            });
            let routeData;
            try { routeData = await routeResp.json(); }
            catch(e) { console.error('Route API returned non-JSON (is osmnx installed in Docker?)'); gsSetStatus('Routing error — check server logs', false); gensetShowNotFound(); return; }
            if (!routeResp.ok || routeData.error) { gsSetStatus('Routing error: ' + (routeData?.error||routeResp.status), false); gensetShowNotFound(); return; }

            const valid = routeData.results; // already sorted by road_dist_m
            if (!valid || valid.length === 0) { gensetShowNotFound(); return; }

            // Draw all routes, store polylines for interactive selection
            gensetRouteLines = [];
            valid.forEach((r, i) => {
                if (!r.route_coords || r.route_coords.length < 2) return;
                const line = L.polyline(r.route_coords, {
                    color:     i === 0 ? '#f59e0b' : '#4ade80',
                    weight:    i === 0 ? 6 : 3,
                    opacity:   i === 0 ? 0.95 : 0.45,
                    dashArray: i === 0 ? null : '8 5',
                    lineCap: 'round', lineJoin: 'round', pane: 'routePane'
                }).addTo(gensetMap);
                gensetRouteLines.push(line);
                gensetTnbMarkers.push(line); // also store for cleanup
            });
            const allCoords = valid.flatMap(r => r.route_coords || []);
            if (allCoords.length) gensetMap.fitBounds(L.latLngBounds(allCoords).pad(0.12));

            // ── Build result HTML with clickable path rows ─────────────────
            document.getElementById('gensetLoading').style.display = 'none';
            document.getElementById('gensetFound').style.display   = 'block';

            // Store valid paths for selection
            window._gsValidPaths = valid;

            let pathsHtml = valid.map((r, i) => `
                <div id="gsPathRow-${i}" onclick="gsSelectPath(${i})"
                     style="display:flex;align-items:center;justify-content:space-between;padding:6px 7px;border-bottom:1px solid #d1fae5;font-size:0.7rem;cursor:pointer;border-radius:${i===0?'6px':'0'};background:${i===0?'#fef3c7':'transparent'};transition:background .12s;"
                     onmouseover="if(${i}!==window._gsActivePathIdx)this.style.background='#f0fdf4'" onmouseout="if(${i}!==window._gsActivePathIdx)this.style.background='transparent'">
                    <div style="display:flex;align-items:center;gap:5px;">
                        <span style="background:${i===0?'#d97706':'#16a34a'};color:white;border-radius:8px;font-size:0.55rem;font-weight:800;padding:1px 5px;">#${i+1}</span>
                        <span style="color:#374151;font-weight:${i===0?'700':'400'};">${r.name}</span>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-weight:800;color:${i===0?'#d97706':'#16a34a'};white-space:nowrap;">${r.road_dist_km} km</div>
                        <div style="font-size:0.58rem;color:#9ca3af;">${r.road_dist_m.toFixed(0)} m</div>
                    </div>
                </div>`).join('');

            window._gsActivePathIdx = 0;
            document.getElementById('gensetFoundContent').innerHTML = `
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
                    <i class="fas fa-bolt" style="color:#d97706;"></i>
                    <b style="color:#92400e;font-size:0.78rem;">${valid.length} path${valid.length>1?'s':''} within 2 km</b>
                    ${valid.length>1?'<span style="font-size:0.6rem;color:#6b7280;margin-left:auto;">click to select</span>':''}
                </div>
                <div style="max-height:180px;overflow-y:auto;border:1px solid #d1fae5;border-radius:7px;padding:2px;">${pathsHtml}</div>
                <div style="margin-top:6px;font-size:0.62rem;color:#6b7280;">
                    <i class="fas fa-info-circle" style="color:#16a34a;margin-right:3px;"></i>
                    Routed in ${routeData.elapsed_s}s via OSMnx road network
                </div>`;

            gsSetStatus('Done — ' + valid.length + ' path' + (valid.length>1?'s':'') + ' found', false);
            setTimeout(() => gsSetStatus(null), 2500);

        } catch(err) {
            console.error('Genset TNB search error:', err);
            gsSetStatus('Error', false);
            gensetShowNotFound();
        } finally {
            document.getElementById('gensetFindBtn').disabled = false;
            document.getElementById('gensetLoading').style.display = 'none';
        }
    }

    function gensetShowNotFound() {
        document.getElementById('gensetLoading').style.display  = 'none';
        document.getElementById('gensetFound').style.display    = 'none';
        document.getElementById('gensetNotFound').style.display = 'block';
        document.getElementById('gensetFindBtn').disabled       = false;
        gsSetStatus(null);
    }

    // ── Search autocomplete ──
    function gensetOnSearch(val) {
        const box = document.getElementById('gensetSearchResults');
        if (!val || val.length < 2) { box.style.display = 'none'; return; }
        const q    = val.toLowerCase();
        const hits = gensetSitesData.filter(s =>
            s.site_id.toLowerCase().includes(q) || (s.site_name && s.site_name.toLowerCase().includes(q))
        ).slice(0, 10);
        if (!hits.length) { box.style.display = 'none'; return; }
        box.innerHTML = hits.map(s =>
            `<div onclick="gensetSearchSelect('${s.site_id}')"
                  style="padding:7px 12px;cursor:pointer;font-size:0.8rem;color:#374151;border-bottom:1px solid #f3f4f6;"
                  onmouseover="this.style.background='#f0fdf4'" onmouseout="this.style.background=''">
                <b>${s.site_id}</b>${s.site_name ? ' — ' + s.site_name : ''}
             </div>`
        ).join('');
        box.style.display = 'block';
    }

    function gensetSearchSelect(siteId) {
        const site = gensetSitesData.find(s => s.site_id === siteId);
        if (!site) return;
        document.getElementById('gensetSearchResults').style.display = 'none';
        document.getElementById('gensetSearchInput').value = site.site_id;
        gensetSelectSite(site, gensetSiteMarkers[site.site_id] || { setIcon: () => {} });
    }

    // ── Geo Tools (genset-scoped, using gs* IDs) ──
    function gsStartGeoPick(point) {
        gsGeoPickMode = point;
        const el = document.getElementById('gsGeoPickStatus');
        document.getElementById('gsGeoPickText').textContent = `Click map to set Point ${point}`;
        el.style.display = 'flex';
        if (gensetMap) gensetMap.getContainer().style.cursor = 'crosshair';
    }

    function gsSetGeoPoint(lat, lng, point) {
        if (point === 'A') {
            document.getElementById('gsGeoLatA').value = lat.toFixed(6);
            document.getElementById('gsGeoLngA').value = lng.toFixed(6);
        } else {
            document.getElementById('gsGeoLatB').value = lat.toFixed(6);
            document.getElementById('gsGeoLngB').value = lng.toFixed(6);
        }
        // Place marker
        const color = point === 'A' ? '#2563eb' : '#dc2626';
        const ref   = point === 'A' ? 'gsGeoMarkerA' : 'gsGeoMarkerB';
        if (window[ref]) gensetMap.removeLayer(window[ref]);
        window[ref] = L.circleMarker([lat, lng], {
            radius: 7, color, fillColor: color, fillOpacity: 1, weight: 2.5, interactive: false
        }).bindTooltip(`<b>${point}</b>`, { permanent: true, direction: 'top', offset: [0, -8], opacity: 1 }).addTo(gensetMap);

        gsGeoPickMode = null;
        document.getElementById('gsGeoPickStatus').style.display = 'none';
        if (gensetMap) gensetMap.getContainer().style.cursor = '';
        gsRunGeoCalc();
    }

    function gsRunGeoCalc() {
        const latA = parseFloat(document.getElementById('gsGeoLatA').value);
        const lngA = parseFloat(document.getElementById('gsGeoLngA').value);
        const latB = parseFloat(document.getElementById('gsGeoLatB').value);
        const lngB = parseFloat(document.getElementById('gsGeoLngB').value);
        if (isNaN(latA) || isNaN(lngA) || isNaN(latB) || isNaN(lngB)) {
            document.getElementById('gsGeoResults').style.display = 'none'; return;
        }
        const dist    = haversineDistance(latA, lngA, latB, lngB);
        const bearing = calculateBearing(latA, lngA, latB, lngB);
        const back    = (bearing + 180) % 360;
        const distStr = dist >= 1000 ? (dist / 1000).toFixed(3) + ' km' : dist.toFixed(1) + ' m';
        document.getElementById('gsGeoDistResult').textContent   = distStr;
        document.getElementById('gsGeoBearingResult').textContent = bearing.toFixed(2) + '°';
        document.getElementById('gsGeoBackBearing').textContent   = back.toFixed(2) + '°';
        document.getElementById('gsGeoResults').style.display    = 'block';

        if (gsGeoLine) gensetMap.removeLayer(gsGeoLine);
        gsGeoLine = L.polyline([[latA, lngA], [latB, lngB]], {
            color: '#7c3aed', weight: 2, dashArray: '6 5', opacity: 0.85,
            interactive: false, pane: 'routePane'
        }).addTo(gensetMap);

        // Refresh point markers with latest typed values
        const mkA = window['gsGeoMarkerA'], mkB = window['gsGeoMarkerB'];
        if (mkA) { gensetMap.removeLayer(mkA); window['gsGeoMarkerA'] = L.circleMarker([latA, lngA], { radius:7, color:'#2563eb', fillColor:'#3b82f6', fillOpacity:1, weight:2.5, interactive:false }).bindTooltip('<b>A</b>',{permanent:true,direction:'top',offset:[0,-8],opacity:1}).addTo(gensetMap); }
        if (mkB) { gensetMap.removeLayer(mkB); window['gsGeoMarkerB'] = L.circleMarker([latB, lngB], { radius:7, color:'#dc2626', fillColor:'#ef4444', fillOpacity:1, weight:2.5, interactive:false }).bindTooltip('<b>B</b>',{permanent:true,direction:'top',offset:[0,-8],opacity:1}).addTo(gensetMap); }
    }

    function gsClearGeoTools() {
        ['gsGeoMarkerA','gsGeoMarkerB'].forEach(ref => { if (window[ref]) { gensetMap.removeLayer(window[ref]); window[ref] = null; } });
        if (gsGeoLine) { gensetMap.removeLayer(gsGeoLine); gsGeoLine = null; }
        ['gsGeoLatA','gsGeoLngA','gsGeoLatB','gsGeoLngB'].forEach(id => { document.getElementById(id).value = ''; });
        document.getElementById('gsGeoResults').style.display = 'none';
        gsGeoPickMode = null;
        document.getElementById('gsGeoPickStatus').style.display = 'none';
        if (gensetMap) gensetMap.getContainer().style.cursor = '';
    }

    // ── Bulk Excel processing ──
    let gsBulkSiteIds    = [];
    let gsBulkResults    = [];   // { siteId, status, paths:[], bestPath, lat, lng }
    let gsBulkRunning    = false;
    let gsBulkAbort      = false;
    let gsBulkRouteLines = []; // polylines drawn on map
    let gsBulkExpanded   = new Set(); // expanded site IDs in accordion

    function gensetXlsxLoaded(input) {
        const file = input.files[0];
        if (!file) return;
        document.getElementById('gensetXlsxName').textContent = file.name;
        const reader = new FileReader();
        reader.onload = function(e) {
            try {
                const wb   = XLSX.read(e.target.result, { type: 'array' });
                const ws   = wb.Sheets[wb.SheetNames[0]];
                const rows = XLSX.utils.sheet_to_json(ws, { defval: '' });
                const headers = rows.length ? Object.keys(rows[0]) : [];
                const col = headers.find(h => h.toLowerCase().replace(/[\s_-]/g,'') === 'siteid') || headers[0];
                gsBulkSiteIds = rows.map(r => String(r[col] || '').trim()).filter(Boolean);
                gsBulkResults = [];
                const btn = document.getElementById('gensetBulkRunBtn');
                if (gsBulkSiteIds.length) {
                    btn.disabled = false; btn.style.background = '#16a34a'; btn.style.color = 'white'; btn.style.cursor = 'pointer';
                    gsSetStatus(gsBulkSiteIds.length + ' sites loaded from file', false);
                    setTimeout(() => gsSetStatus(null), 2500);
                } else { alert('No site_id values found in the first sheet.'); }
            } catch(err) { alert('Could not read file: ' + err.message); }
        };
        reader.readAsArrayBuffer(file);
    }

    async function gensetRunBulk() {
        if (!gsBulkSiteIds.length || gsBulkRunning) return;
        if (!gensetSitesData.length) { alert('Sites not loaded yet — please wait.'); return; }
        gsBulkRunning = true; gsBulkAbort = false; gsBulkResults = [];
        // Reset UI for re-run
        ['gsBulkTotalRow','gsBulkCostCalc','gsBulkExportRow'].forEach(id => { const el=document.getElementById(id); if(el) el.style.display='none'; });
        gsBulkRouteLines.forEach(l => { try { gensetMap.removeLayer(l); } catch(e) {} });
        gsBulkRouteLines = [];

        const total = gsBulkSiteIds.length;
        const progress = document.getElementById('gensetBulkProgress');
        const progressBar = document.getElementById('gensetBulkProgressBar');
        const progressLbl = document.getElementById('gensetBulkProgressLabel');
        const progressPct = document.getElementById('gensetBulkProgressPct');
        const resultsEl = document.getElementById('gensetBulkResults');
        const statsEl = document.getElementById('gensetBulkStats');
        const runBtn = document.getElementById('gensetBulkRunBtn');
        const stopBtn = document.getElementById('gensetBulkStopBtn');

        progress.style.display = 'block'; resultsEl.style.display = 'block';
        statsEl.style.display = 'grid';
        stopBtn.style.display = 'inline-flex'; runBtn.style.display = 'none';
        resultsEl.innerHTML = '';
        resultsEl.insertAdjacentHTML('afterbegin',
            '<div style="display:grid;grid-template-columns:1fr 1.4fr 0.7fr;gap:4px;padding:4px 8px;background:#f1f5f9;font-weight:800;font-size:0.6rem;color:#475569;text-transform:uppercase;border-bottom:1px solid #e2e8f0;"><span>Site ID</span><span>Substation</span><span>Distance</span></div>');

        let found = 0, none = 0, totalDistM = 0, totalPathCount = 0;

        try {
        for (let i = 0; i < total; i++) {
            if (gsBulkAbort) break;
            const siteId = gsBulkSiteIds[i];
            const pct = Math.round((i / total) * 100);
            progressBar.style.width = pct + '%'; progressPct.textContent = pct + '%';
            progressLbl.textContent = 'Processing ' + (i+1) + '/' + total + ': ' + siteId;
            gsSetStatus((i+1) + '/' + total + ' — ' + siteId);

            const site = gensetSitesData.find(s => s.site_id.toUpperCase() === siteId.toUpperCase());
            if (!site) {
                const row = { siteId, status:'not_found', subName:'Site not in DB', distM:null, routeGeom:null, lat:null, lng:null };
                gsBulkResults.push(row); none++;
                gensetBulkAppendRow(resultsEl, row, i);
            } else {
                try {
                    const paths = await gensetFindTNBSilentFull(site.lat, site.lng);
                    if (paths && paths.length) {
                        found++;
                        totalPathCount += paths.length;
                        const best = paths[0];
                        totalDistM += best.road_dist_m;
                        const row = { siteId, status:'found', paths, bestPath:best, lat: site.lat, lng: site.lng };
                        gsBulkResults.push(row);
                        gensetBulkAppendRow(resultsEl, row, i);
                        // Draw best path solid, others dashed + place substation markers
                        paths.forEach((p, pi) => {
                            if (!p.route_coords || p.route_coords.length < 2) return;
                            const line = L.polyline(p.route_coords, {
                                color: pi===0?'#f59e0b':'#4ade80', weight: pi===0?4:2.5,
                                opacity: pi===0?0.85:0.5, dashArray: pi===0?null:'7 4',
                                lineCap:'round', pane:'routePane'
                            }).addTo(gensetMap);
                            gsBulkRouteLines.push(line);
                            // Substation marker
                            const subM = L.marker([p.lat, p.lng], { icon: GENSET_TNB_ICON })
                                .bindPopup('<b style="color:#d97706;">'+p.name+'</b><br>'
                                    +'<span style="font-size:0.72rem;color:#6b7280;">Road dist: <b>'+p.road_dist_km+' km</b><br>'
                                    +'Site: '+siteId+'</span>')
                                .addTo(gensetMap);
                            gsBulkRouteLines.push(subM); // store for cleanup
                        });
                    } else {
                        none++;
                        const row = { siteId, status:'none', paths:[], bestPath:null, lat: site.lat, lng: site.lng };
                        gsBulkResults.push(row); gensetBulkAppendRow(resultsEl, row, i);
                    }
                } catch {
                    none++;
                    const row = { siteId, status:'error', subName:'Error', distM:null, routeGeom:null, lat: site.lat, lng: site.lng };
                    gsBulkResults.push(row); gensetBulkAppendRow(resultsEl, row, i);
                }
            }
            gensetBulkUpdateStats(total, found, none, totalDistM, totalPathCount);
            await new Promise(r => setTimeout(r, 700));
        }
        } finally {
            gsBulkRunning = false;
        }

        progressBar.style.width = '100%'; progressPct.textContent = '100%';
        progressLbl.textContent = gsBulkAbort ? 'Stopped.' : 'Complete!';
        gsSetStatus(gsBulkAbort ? 'Stopped' : 'Done — ' + found + '/' + total + ' found', false);
        setTimeout(() => gsSetStatus(null), 3000);
        gsBulkRunning = false;
        // Re-enable run button so user can run again
        runBtn.disabled = false;
        runBtn.style.background = '#16a34a'; runBtn.style.color = 'white'; runBtn.style.cursor = 'pointer';
        runBtn.style.display = 'inline-flex'; stopBtn.style.display = 'none';

        if (gsBulkRouteLines.length > 0) {
            const group = L.featureGroup(gsBulkRouteLines);
            gensetMap.fitBounds(group.getBounds(), { padding: [40, 40] });
        }
        if (found > 0) {
            document.getElementById('gsBulkTotalRow').style.display  = 'block';
            document.getElementById('gsBulkCostCalc').style.display  = 'block';
            document.getElementById('gsBulkExportRow').style.display = 'flex';
        }
        gsBulkRecalcCost();
    }

    async function gensetFindTNBSilentFull(lat, lng) {
        // Step 1: Overpass (browser-direct)
        const q = '[out:json][timeout:20];(node["power"="substation"](around:2000,'+lat+','+lng+');way["power"="substation"](around:2000,'+lat+','+lng+');relation["power"="substation"](around:2000,'+lat+','+lng+'););out center;';
        const ovData = await (await fetch('https://overpass-api.de/api/interpreter', { method:'POST', body:q })).json();
        const substations = (ovData.elements||[]).map(el=>({
            osm_id: String(el.id),
            name:   el.tags&&(el.tags.name||el.tags['name:en']||el.tags.operator)||'TNB Substation',
            lat:    el.type==='node'?el.lat:(el.center&&el.center.lat),
            lng:    el.type==='node'?el.lon:(el.center&&el.center.lon)
        })).filter(s=>s.lat&&s.lng);
        if (!substations.length) return null;

        // Step 2: OSMnx routing via backend (returns all paths)
        const resp = await fetch('/api/genset/route', {
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ lat, lng, substations })
        });
        let data;
        try { data = await resp.json(); }
        catch(e) { console.error('Route API non-JSON response', e); return null; }
        if (!resp.ok || data.error || !data.results || !data.results.length) return null;
        // Returns all paths sorted by distance
        return data.results;
    }

    function gensetBulkAppendRow(container, row, idx) {
        const bg    = idx%2===0 ? '#ffffff' : '#f9fafb';
        const color = row.status==='found' ? '#16a34a' : row.status==='none' ? '#dc2626' : '#9ca3af';
        const icon  = row.status==='found' ? 'fa-check-circle' : row.status==='none' ? 'fa-times-circle' : 'fa-question-circle';
        const pathCount = row.paths ? row.paths.length : 0;
        const bestDist  = row.bestPath ? row.bestPath.road_dist_km + ' km' : '—';
        const rowId     = 'gsbr-' + idx;

        const wrap = document.createElement('div');
        wrap.style.cssText = 'border-bottom:1px solid #f3f4f6;background:'+bg+';';

        // Main row
        const mainRow = document.createElement('div');
        mainRow.style.cssText = 'display:grid;grid-template-columns:1fr auto auto;gap:6px;align-items:center;padding:5px 8px;cursor:pointer;';
        mainRow.title = 'Click to expand / fly to site';
        mainRow.innerHTML =
            '<span style="font-weight:700;color:#1e293b;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.7rem;" title="'+row.siteId+'">'+row.siteId+'</span>'
            + '<span style="font-weight:700;color:'+(row.bestPath?'#d97706':color)+';font-size:0.7rem;white-space:nowrap;">'
            +   '<i class="fas '+icon+'" style="font-size:0.58rem;color:'+color+';margin-right:3px;"></i>'+bestDist
            + '</span>'
            + (pathCount > 0
                ? '<span id="'+rowId+'-badge" style="background:#dbeafe;color:#1d4ed8;border-radius:8px;font-size:0.58rem;font-weight:800;padding:1px 6px;white-space:nowrap;">▸ '+pathCount+' path'+( pathCount>1?'s':'')+'</span>'
                : '<span style="font-size:0.58rem;color:#9ca3af;">—</span>');

        mainRow.onclick = () => {
            if (row.lat) gensetMap.flyTo([row.lat, row.lng], 15, { animate:true, duration:1.0 });
            if (!pathCount) return;
            const detail = document.getElementById(rowId+'-detail');
            const badge  = document.getElementById(rowId+'-badge');
            const open   = detail.style.display !== 'none';
            detail.style.display = open ? 'none' : 'block';
            if (badge) badge.textContent = (open ? '▸ ' : '▾ ') + pathCount + ' path' + (pathCount>1?'s':'');
        };

        wrap.appendChild(mainRow);

        // Expandable path detail
        if (pathCount > 0) {
            const detail = document.createElement('div');
            detail.id = rowId + '-detail';
            detail.style.cssText = 'display:none;background:#f0fdf4;border-top:1px solid #dcfce7;padding:4px 8px 6px 16px;';
            detail.innerHTML = row.paths.map((p, pi) =>
                '<div style="display:flex;justify-content:space-between;align-items:center;padding:2px 0;font-size:0.65rem;border-bottom:1px solid #dcfce7;">'
                + '<span style="color:#374151;font-weight:'+(pi===0?'700':'400')+';">'
                + '<span style="background:'+(pi===0?'#d97706':'#16a34a')+';color:white;border-radius:6px;font-size:0.5rem;font-weight:800;padding:1px 4px;margin-right:4px;">#'+(pi+1)+'</span>'
                + p.name+'</span>'
                + '<span style="font-weight:800;color:'+(pi===0?'#d97706':'#16a34a')+';white-space:nowrap;">'+p.road_dist_km+' km</span>'
                + '</div>'
            ).join('');
            wrap.appendChild(detail);
        }

        container.appendChild(wrap);
        container.scrollTop = container.scrollHeight;
    }

    function gensetBulkUpdateStats(total, found, none, totalDistM, totalPaths) {
        document.getElementById('gsBulkStatTotal').textContent = total;
        document.getElementById('gsBulkStatFound').textContent = found;
        document.getElementById('gsBulkStatNone').textContent  = none;
        document.getElementById('gsBulkStatPaths').textContent = totalPaths || 0;
        if (totalDistM > 0) {
            document.getElementById('gsBulkTotalRow').style.display = 'block';
            document.getElementById('gsBulkTotalDist').textContent  = (totalDistM/1000).toFixed(3) + ' km';
        }
        gsBulkRecalcCost();
    }

    function gsBulkRecalcCost() {
        // Sum of best path per site
        const totalDistM = gsBulkResults.filter(r=>r.bestPath).reduce((s,r)=>s+r.bestPath.road_dist_m, 0);
        const perMat     = parseFloat(document.getElementById('gsBulkCostPer100m')?.value) || 0;
        const perEng     = parseFloat(document.getElementById('gsBulkEngPer100m')?.value)  || 0;
        const units      = totalDistM / 100;
        const matCost    = units * perMat;
        const engCost    = units * perEng;
        const total      = matCost + engCost;
        const fmt = v => 'RM ' + v.toLocaleString('en-MY', {minimumFractionDigits:2, maximumFractionDigits:2});
        const matEl  = document.getElementById('gsBulkMatCost');  if (matEl)  matEl.textContent  = fmt(matCost);
        const engEl  = document.getElementById('gsBulkEngCost');  if (engEl)  engEl.textContent  = fmt(engCost);
        const totEl  = document.getElementById('gsBulkTotalCost'); if (totEl) totEl.textContent  = fmt(total);
    }

    function gensetStopBulk() { gsBulkAbort = true; }

    function gensetClearBulk() {
        gsBulkAbort = true; gsBulkRunning = false; gsBulkSiteIds = []; gsBulkResults = [];
        gsBulkRouteLines.forEach(l => { try { gensetMap.removeLayer(l); } catch(e) {} });
        gsBulkRouteLines = [];
        const ids = ['gensetXlsxName','gensetBulkProgress','gensetBulkResults','gensetBulkStats','gsBulkTotalRow','gsBulkCostCalc','gsBulkExportRow'];
        document.getElementById('gensetXlsxName').textContent = 'Choose .xlsx / .xls / .csv…';
        document.getElementById('gensetXlsxInput').value = '';
        ['gensetBulkProgress','gensetBulkResults','gensetBulkStats','gsBulkTotalRow','gsBulkCostCalc','gsBulkExportRow'].forEach(id => { const el=document.getElementById(id); if(el) el.style.display='none'; });
        document.getElementById('gensetBulkResults').innerHTML = '';
        document.getElementById('gensetBulkProgressBar').style.width = '0%';
        const cpi = document.getElementById('gsBulkCostPer100m'); if(cpi) cpi.value = '';
        const epi = document.getElementById('gsBulkEngPer100m');  if(epi) epi.value  = '';
        ['gsBulkMatCost','gsBulkEngCost','gsBulkTotalCost'].forEach(id => { const el=document.getElementById(id); if(el) el.textContent='RM 0.00'; });
        const btn = document.getElementById('gensetBulkRunBtn');
        btn.disabled = true; btn.style.background='#d1d5db'; btn.style.color='#9ca3af'; btn.style.cursor='not-allowed';
        btn.style.display='inline-flex'; document.getElementById('gensetBulkStopBtn').style.display='none';
        gsSetStatus(null);
    }

    function gensetBulkExport(format, mode) {
        const perMat = parseFloat(document.getElementById('gsBulkCostPer100m')?.value) || 0;
        const perEng = parseFloat(document.getElementById('gsBulkEngPer100m')?.value)  || 0;
        const applicable = gsBulkResults.filter(r => r.status === 'found' && r.bestPath);

        if (mode === 'all') {
            // ── All paths: one row per site×path, no totals ───────────────
            const rows = [];
            applicable.forEach(r => {
                r.paths.forEach((p, pi) => {
                    const units = p.road_dist_m / 100;
                    rows.push({
                        site_id:             r.siteId,
                        site_lat:            r.lat,
                        site_lng:            r.lng,
                        rank:                pi + 1,
                        substation:          p.name,
                        osm_id:              p.osm_id || '',
                        substation_lat:      p.lat,
                        substation_lng:      p.lng,
                        road_distance_km:    p.road_dist_km,
                        material_cost_rm:    (units * perMat).toFixed(2),
                        engineering_cost_rm: (units * perEng).toFixed(2),
                        total_cost_rm:       (units * (perMat + perEng)).toFixed(2),
                    });
                });
            });
            const ws = XLSX.utils.json_to_sheet(rows);
            const wb = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(wb, ws, 'All Paths');
            XLSX.writeFile(wb, 'genset_all_paths.xlsx');
            return;
        }

        // ── Best path: one row per site, totals row at bottom ─────────────
        const rows = applicable.map(r => {
            const p     = r.bestPath;
            const units = p.road_dist_m / 100;
            return {
                site_id:             r.siteId,
                site_lat:            r.lat,
                site_lng:            r.lng,
                substation:          p.name,
                osm_id:              p.osm_id || '',
                substation_lat:      p.lat,
                substation_lng:      p.lng,
                road_distance_km:    p.road_dist_km,
                material_cost_rm:    (units * perMat).toFixed(2),
                engineering_cost_rm: (units * perEng).toFixed(2),
                total_cost_rm:       (units * (perMat + perEng)).toFixed(2),
            };
        });
        const totalDistM = applicable.reduce((s,r) => s + r.bestPath.road_dist_m, 0);
        const totalUnits = totalDistM / 100;
        rows.push({
            site_id:             'TOTAL',
            substation:          '',
            road_distance_km:    (totalDistM/1000).toFixed(3),
            material_cost_rm:    (totalUnits * perMat).toFixed(2),
            engineering_cost_rm: (totalUnits * perEng).toFixed(2),
            total_cost_rm:       (totalUnits * (perMat + perEng)).toFixed(2),
        });

        if (format === 'csv') {
            const header = Object.keys(rows[0]).join(',') + '\n';
            const body   = rows.map(r => Object.values(r).join(',')).join('\n');
            const a = document.createElement('a');
            a.href = URL.createObjectURL(new Blob([header+body],{type:'text/csv'}));
            a.download = 'genset_best_paths.csv'; a.click();
        } else {
            const ws = XLSX.utils.json_to_sheet(rows);
            const wb = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(wb, ws, 'Best Paths');
            XLSX.writeFile(wb, 'genset_best_paths.xlsx');
        }
    }

    // ── Right-click coord copy (coverage + genset maps) ──────────────────────
    let _ctxLat = null, _ctxLng = null;

    function _attachCtxMenu(leafletMap) {
        leafletMap.on('contextmenu', function(e) {
            e.originalEvent.preventDefault();
            _ctxLat = e.latlng.lat;
            _ctxLng = e.latlng.lng;
            const label = _ctxLat.toFixed(6) + ', ' + _ctxLng.toFixed(6);
            document.getElementById('ctxCoordLabel').textContent = label;
            const menu = document.getElementById('mapContextMenu');
            menu.style.display = 'block';
            menu.style.left = e.originalEvent.clientX + 'px';
            menu.style.top  = e.originalEvent.clientY + 'px';
            requestAnimationFrame(() => {
                const r = menu.getBoundingClientRect();
                if (r.right  > window.innerWidth)  menu.style.left = (e.originalEvent.clientX - r.width)  + 'px';
                if (r.bottom > window.innerHeight)  menu.style.top  = (e.originalEvent.clientY - r.height) + 'px';
            });
        });
        leafletMap.on('mousedown', () => _hideCtxMenu());
    }

    function _hideCtxMenu() { document.getElementById('mapContextMenu').style.display = 'none'; }
    document.addEventListener('click', _hideCtxMenu);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') _hideCtxMenu(); });

    function _toDMS(deg) {
        const d = Math.floor(Math.abs(deg)), m = Math.floor((Math.abs(deg)-d)*60);
        const s = ((Math.abs(deg)-d-m/60)*3600).toFixed(1);
        return d + '° ' + m + "' " + s + '"';
    }
    function ctxCopyLatLng() {
        if (_ctxLat===null) return;
        navigator.clipboard.writeText(_ctxLat.toFixed(6) + ', ' + _ctxLng.toFixed(6))
            .then(() => _ctxToast('Copied: ' + _ctxLat.toFixed(5) + ', ' + _ctxLng.toFixed(5)));
        _hideCtxMenu();
    }
    function ctxCopyLngLat() {
        if (_ctxLat===null) return;
        navigator.clipboard.writeText(_ctxLng.toFixed(6) + ', ' + _ctxLat.toFixed(6))
            .then(() => _ctxToast('Copied: ' + _ctxLng.toFixed(5) + ', ' + _ctxLat.toFixed(5)));
        _hideCtxMenu();
    }
    function ctxCopyDMS() {
        if (_ctxLat===null) return;
        const latH = _ctxLat >= 0 ? 'N' : 'S', lngH = _ctxLng >= 0 ? 'E' : 'W';
        const txt = _toDMS(_ctxLat)+latH + ' ' + _toDMS(_ctxLng)+lngH;
        navigator.clipboard.writeText(txt).then(() => _ctxToast('Copied: ' + txt));
        _hideCtxMenu();
    }
    function _ctxToast(msg) {
        let t = document.getElementById('ctxToast');
        if (!t) { t = document.createElement('div'); t.id='ctxToast'; t.style.cssText='position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#14532d;color:#fff;padding:8px 18px;border-radius:20px;font-size:0.75rem;font-weight:700;z-index:99999;pointer-events:none;transition:opacity .3s;'; document.body.appendChild(t); }
        t.textContent = msg; t.style.opacity = '1';
        clearTimeout(t._tid);
        t._tid = setTimeout(() => { t.style.opacity = '0'; }, 2000);
    }

    // ── ATOM on main JEJAK shell (mhMap = Coverage Map) ─────────────────────
    function atomTargetMapJeak() {
        if (typeof mhMap !== 'undefined' && mhMap) return mhMap;
        if (typeof map !== 'undefined' && map) return map;
        return null;
    }
    var atomHullLayerJeak = null, atomPointLayerJeak = null, atomCentroidLayerJeak = null;
    var atomHullsOnJeak = true, atomPointsOnJeak = false, atomCentroidsOnJeak = true;

    function toggleAtomPanel() {
        const p = document.getElementById('atomPanel');
        if (p) p.classList.toggle('open');
    }

    async function runAtomJeak() {
        const LMap = atomTargetMapJeak();
        if (!LMap) {
            alert('Open the Coverage Map tab first (satellite view with cell sites).');
            return;
        }
        const ws = document.getElementById('weekSelect');
        const rs = document.getElementById('regionSelect');
        const region = rs ? (rs.value || 'All') : 'All';
        const week = (ws && ws.value) ? String(ws.value) : null;

        const btn = document.getElementById('atomRunBtn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="atom-spinner-inline"></span> Running…'; }
        clearAtomLayersJeak();

        const listHost = document.getElementById('atomClusterList');
        if (listHost) listHost.innerHTML = '<div class="text-center text-violet-600 py-10 text-xs font-semibold"><span class="atom-spinner-inline" style="width:26px;height:26px;border-width:3px;border-color:rgba(67,56,202,0.3);border-top-color:#4338ca"></span><div class="mt-3">AutoDBSCAN…</div></div>';

        try {
            const res = await fetch('/api/atom/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ region: region, week: week })
            });
            const data = await res.json();
            if (!data.success) {
                atomShowErrorJeak(data.error || 'Pipeline failed');
                return;
            }
            renderAtomResultsJeak(data, LMap);
        } catch (e) {
            atomShowErrorJeak(e.message || 'Network error');
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-play"></i> Run ATOM Analysis'; }
        }
    }

    function renderAtomResultsJeak(data, LMap) {
        const params = data.params, summaries = data.cluster_summaries || [];
        const geojson = data.geojson, pj = data.points_geojson;

        const chips = document.getElementById('atomParamChips');
        if (chips) chips.innerHTML = '<span class="atom-param-chip">ε ' + params.eps + '</span><span class="atom-param-chip">minPts ' + params.min_pts + '</span>';

        document.getElementById('atomStatClusters').textContent = data.n_clusters;
        document.getElementById('atomStatPoints').textContent = String(data.total_points).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        document.getElementById('atomStatNoise').textContent = String(data.n_noise).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        document.getElementById('atomStatsBar').classList.remove('hidden');
        document.getElementById('atomLayerToggles').classList.remove('hidden');

        atomHullLayerJeak = L.geoJSON(geojson, {
            style: function(feature) {
                const c = feature.properties.color || '#6366f1';
                return { color: c, fillColor: c, fillOpacity: 0.2, weight: 2, dashArray: '4 3' };
            },
            onEachFeature: function(feature, layer) {
                const p = feature.properties;
                layer.bindPopup('<div style="min-width:180px;padding:8px 10px;font-size:12px;"><strong>Cluster ' + p.cluster_id + '</strong><br>' + p.point_count + ' pts · avg RSRP ' + (p.avg_rsrp != null ? p.avg_rsrp + ' dBm' : 'N/A') + '</div>');
            }
        });
        if (atomHullsOnJeak) atomHullLayerJeak.addTo(LMap);

        if (pj && pj.features && pj.features.length) {
            atomPointLayerJeak = L.geoJSON(pj, {
                pointToLayer: function(feature, latlng) {
                    const c = feature.properties.color || '#6366f1';
                    return L.circleMarker(latlng, { radius: 5, fillColor: c, color: '#fff', weight: 1, fillOpacity: 0.88 });
                }
            });
            if (atomPointsOnJeak) atomPointLayerJeak.addTo(LMap);
        } else atomPointLayerJeak = null;

        const listEl = document.getElementById('atomClusterList');
        if (!summaries.length) {
            listEl.innerHTML = '<div class="text-center text-gray-400 py-8 text-xs">No clusters for this filter.</div>';
            return;
        }
        listEl.innerHTML = summaries.map(function(c) {
            var rsrp   = c.avg_rsrp != null ? c.avg_rsrp : 'N/A';
            var npBadge = c.np_id
                ? '<span style="background:#dbeafe;color:#1d4ed8;border-radius:4px;padding:1px 6px;font-size:0.58rem;font-weight:800;letter-spacing:.04em;font-family:monospace;">' + c.np_id + '</span>'
                : '';
            var rolloutBtn = '<button onclick="event.stopPropagation(); atomCreateRollout(' + JSON.stringify(c) + ')" '
                + 'style="margin-left:4px;background:#0369a1;color:white;border:none;border-radius:4px;padding:2px 7px;font-size:0.58rem;font-weight:700;cursor:pointer;" '
                + 'title="Create Rollout plan for this cluster"><i class="fas fa-route"></i></button>';
            return '<div class="atom-cluster-card" onclick="atomFlyToJeak(' + c.center_lat + ',' + c.center_lng + ')">' +
              '<div class="atom-cluster-dot" style="background:' + c.color + '"></div>' +
              '<div class="flex-1 min-w-0">' +
              '<div class="flex justify-between gap-2 flex-wrap">' +
              '<span class="text-xs font-black text-gray-800">Cluster ' + c.cluster_id + ' ' + npBadge + '</span>' +
              '<span class="text-[9px] font-bold px-2 py-0.5 rounded-full shrink-0" style="background:' + c.color + '22;color:#111">' + c.point_count + ' pts</span></div>' +
              '<div class="text-[10px] text-gray-500 mt-0.5 flex items-center gap-1">Avg RSRP: <strong class="text-red-600">' + rsrp + ' dBm</strong>' + rolloutBtn + '</div>' +
              '</div>' +
              '<i class="fas fa-crosshairs text-gray-300 text-xs"></i></div>';
        }).join('');

        // ── Centroid NP-id markers ──────────────────────────────────────────
        if (atomCentroidLayerJeak) { try { LMap.removeLayer(atomCentroidLayerJeak); } catch(e){} }
        atomCentroidLayerJeak = L.layerGroup();
        summaries.forEach(function(c) {
            if (c.center_lat == null || c.center_lng == null) return;
            var npLabel = c.np_id || ('C' + c.cluster_id);
            var iconHtml =
                '<div style="display:flex;flex-direction:column;align-items:center;cursor:pointer;">' +
                '<div style="background:#1e293b;color:white;border:2.5px solid ' + c.color + ';border-radius:8px;' +
                'padding:3px 8px;font-size:0.62rem;font-weight:900;font-family:monospace;white-space:nowrap;' +
                'box-shadow:0 2px 8px rgba(0,0,0,0.45);letter-spacing:.05em;">' + npLabel + '</div>' +
                '<div style="width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;' +
                'border-top:6px solid ' + c.color + ';margin-top:-1px;"></div>' +
                '<div style="width:8px;height:8px;background:' + c.color + ';border-radius:50%;border:2px solid white;' +
                'box-shadow:0 1px 4px rgba(0,0,0,0.4);margin-top:-1px;"></div></div>';
            var m = L.marker([c.center_lat, c.center_lng], {
                icon: L.divIcon({ className: '', html: iconHtml, iconSize: [80, 40], iconAnchor: [40, 40] }),
                zIndexOffset: 900,
            });
            m.bindPopup(
                '<div style="font-family:Inter,sans-serif;min-width:200px;">' +
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">' +
                '<div style="width:14px;height:14px;border-radius:3px;background:' + c.color + ';flex-shrink:0;"></div>' +
                '<span style="font-weight:900;font-size:0.85rem;color:#1e293b;">' + npLabel + '</span>' +
                '&nbsp;<span style="font-size:0.65rem;color:#64748b;">Cluster ' + c.cluster_id + '</span></div>' +
                '<table style="font-size:0.72rem;width:100%;border-collapse:collapse;">' +
                '<tr><td style="color:#6b7280;padding:2px 0;">Points</td><td style="font-weight:700;">' + c.point_count + '</td></tr>' +
                '<tr><td style="color:#6b7280;padding:2px 0;">Avg RSRP</td><td style="font-weight:700;">' + (c.avg_rsrp != null ? c.avg_rsrp + ' dBm' : 'N/A') + '</td></tr>' +
                '<tr><td style="color:#6b7280;padding:2px 0;">Centre</td><td style="font-size:0.62rem;font-weight:600;">' + c.center_lat.toFixed(5) + ', ' + c.center_lng.toFixed(5) + '</td></tr>' +
                '</table>' +
                '<div style="margin-top:8px;display:flex;gap:6px;">' +
                '<button onclick="atomTriggerNova(' + JSON.stringify(c) + ');this.closest(\'.leaflet-popup\').querySelector(\'.leaflet-popup-close-button\').click()" ' +
                'style="flex:1;background:linear-gradient(135deg,#15803d,#16a34a);color:white;border:none;border-radius:6px;padding:5px;font-size:0.6rem;font-weight:700;cursor:pointer;">' +
                '<i class="fas fa-broadcast-tower"></i> NOVA</button>' +
                '<button onclick="atomCreateRollout(' + JSON.stringify(c) + ');this.closest(\'.leaflet-popup\').querySelector(\'.leaflet-popup-close-button\').click()" ' +
                'style="flex:1;background:linear-gradient(135deg,#0c4a6e,#0369a1);color:white;border:none;border-radius:6px;padding:5px;font-size:0.6rem;font-weight:700;cursor:pointer;">' +
                '<i class="fas fa-route"></i> Rollout</button>' +
                '</div></div>'
            );
            m.addTo(atomCentroidLayerJeak);
        });
        if (atomCentroidsOnJeak) atomCentroidLayerJeak.addTo(LMap);

        if (atomHullLayerJeak) {
            try { LMap.fitBounds(atomHullLayerJeak.getBounds(), { padding: [50, 50], maxZoom: 14 }); } catch (err) {}
        }
        if (typeof updateMapLegend === 'function') updateMapLegend();
    }

    function atomFlyToJeak(lat, lng) {
        const m = atomTargetMapJeak();
        if (m) m.flyTo([lat, lng], 14, { animate: true, duration: 1.2 });
    }

    function atomTriggerNova(cluster) {
        // Pre-set NOVA pin to this ATOM cluster centroid and associate the NP-id
        window._novaAtomNpId = cluster.np_id || null;
        if (typeof _novaSetPin === 'function') {
            _novaSetPin(cluster.center_lat, cluster.center_lng);
        } else {
            // Fallback: set internal vars manually
            window._novaPinLat = cluster.center_lat;
            window._novaPinLng = cluster.center_lng;
        }
        if (!_novaOpen) toggleNovaPanel();
        // Show a toast so user knows NOVA is pre-loaded
        var npId = cluster.np_id ? ' (' + cluster.np_id + ')' : '';
        console.log('[NOVA] Triggered from ATOM cluster' + npId + ' at (' + cluster.center_lat + ',' + cluster.center_lng + ')');
    }

    function atomCreateRollout(cluster) {
        rolloutOpenCreate({
            lat:            cluster.center_lat,
            lon:            cluster.center_lng,
            trigger_type:   'NOVA Candidate',
            site_name:      'ATOM Cluster ' + cluster.cluster_id + (cluster.np_id ? ' (' + cluster.np_id + ')' : ''),
            np_id:          cluster.np_id  || null,
            atom_cluster_id: cluster.id   || null,
            nova_run_id:    null,
            nova_label:     null,
        });
        if (!_rolloutOpen) toggleRolloutPanel();
    }

    function toggleAtomHullsJeak() {
        const LMap = atomTargetMapJeak();
        if (!LMap || !atomHullLayerJeak) return;
        atomHullsOnJeak = !atomHullsOnJeak;
        if (atomHullsOnJeak) LMap.addLayer(atomHullLayerJeak); else LMap.removeLayer(atomHullLayerJeak);
        var b = document.getElementById('atomToggleHulls');
        if (b) b.style.opacity = atomHullsOnJeak ? '1' : '0.45';
        if (typeof updateMapLegend === 'function') updateMapLegend();
    }

    function toggleAtomPointsJeak() {
        const LMap = atomTargetMapJeak();
        if (!LMap || !atomPointLayerJeak) return;
        atomPointsOnJeak = !atomPointsOnJeak;
        if (atomPointsOnJeak) LMap.addLayer(atomPointLayerJeak); else LMap.removeLayer(atomPointLayerJeak);
        var b2 = document.getElementById('atomTogglePoints');
        if (b2) b2.style.opacity = atomPointsOnJeak ? '1' : '0.45';
        if (typeof updateMapLegend === 'function') updateMapLegend();
    }

    function clearAtomLayersJeak() {
        var LMap = atomTargetMapJeak();
        if (LMap && atomHullLayerJeak)     { try { LMap.removeLayer(atomHullLayerJeak);     } catch (e) {} }
        if (LMap && atomPointLayerJeak)    { try { LMap.removeLayer(atomPointLayerJeak);    } catch (e) {} }
        if (LMap && atomCentroidLayerJeak) { try { LMap.removeLayer(atomCentroidLayerJeak); } catch (e) {} }
        atomHullLayerJeak = null; atomPointLayerJeak = null; atomCentroidLayerJeak = null;
        var sb = document.getElementById('atomStatsBar'), lb = document.getElementById('atomLayerToggles'), ch = document.getElementById('atomParamChips');
        if (sb) sb.classList.add('hidden');
        if (lb) lb.classList.add('hidden');
        if (ch) ch.innerHTML = '';
    }

    function atomShowErrorJeak(msg) {
        var el = document.getElementById('atomClusterList');
        if (el) el.innerHTML = '<div class="text-center text-red-600 py-8 text-xs font-semibold px-4"><i class="fas fa-exclamation-triangle mr-1"></i>' + String(msg || 'Error') + '</div>';
    }

    async function loadAtomHistoryJeak() {
        try {
            var res = await fetch('/api/atom/history');
            var runs = await res.json();
            if (!runs.length) { alert('No ATOM runs in the database yet.'); return; }
            alert(runs.map(function(r) {
                return '#' + r.id + ' · ' + (r.ran_at || '') + ' · clusters ' + r.n_clusters + ' · eps=' + r.eps + ' · pts=' + r.total_points;
            }).join('\n'));
        } catch (err) { alert('Could not load history: ' + err.message); }
    }

    (function initAtomPanelResizeJeak() {
        var handle = document.getElementById('atomResizeHandle'), panel = document.getElementById('atomPanel');
        if (!handle || !panel) return;
        var drag = false, sx = 0, sw = 0;
        handle.addEventListener('mousedown', function(ev) {
            drag = true; sx = ev.clientX; sw = panel.offsetWidth; handle.classList.add('dragging'); ev.preventDefault();
        });
        document.addEventListener('mousemove', function(ev) {
            if (!drag) return;
            panel.style.width = Math.max(300, Math.min(580, sw - (ev.clientX - sx))) + 'px';
        });
        document.addEventListener('mouseup', function() { drag = false; handle.classList.remove('dragging'); });
    })();

    // ═══════════════════════════════════════════════════════════════
    //  NOVA — New tower site candidate finder
    // ═══════════════════════════════════════════════════════════════

    var _novaOpen = false;
    var _novaPinLat = null, _novaPinLng = null;
    var _novaPinning = false;
    var _novaPinMarker = null;
    var _novaCircleLayer = null;
    var _novaDelaunayLayer = null;
    var _novaCandidateLayer = null;
    var _novaDelaunayOn = true;
    var _novaCandidatesOn = true;
    var _novaRadiusM = 500;
    var _novaRunId = null;   // captured after each NOVA run for Rollout linkage

    function toggleNovaPanel() {
        var p = document.getElementById('novaPanel');
        if (!p) return;
        _novaOpen = !_novaOpen;
        if (_novaOpen) { p.classList.add('open'); }
        else { p.classList.remove('open'); }
    }

    function novaTargetMap() {
        // Try known map variables first
        if (typeof mhMap !== 'undefined' && mhMap) return mhMap;
        if (typeof coverageMap !== 'undefined' && coverageMap) return coverageMap;
        if (typeof map !== 'undefined' && map) return map;
        // Fallback: find any initialised Leaflet container in the DOM
        var containers = document.querySelectorAll('.leaflet-container');
        for (var i = 0; i < containers.length; i++) {
            var m = containers[i]._leaflet_map;
            if (m) return m;
        }
        return null;
    }

    function novaUpdateRadius(val) {
        _novaRadiusM = parseInt(val);
        var lbl = document.getElementById('novaRadiusLabel');
        if (lbl) lbl.textContent = _novaRadiusM >= 1000 ? (_novaRadiusM / 1000).toFixed(1) + ' km' : _novaRadiusM + ' m';
        // Redraw circle if pin exists
        if (_novaPinLat !== null) _novaDrawCircle(_novaPinLat, _novaPinLng, _novaRadiusM);
    }

    function novaSetRadius(m) {
        _novaRadiusM = m;
        var slider = document.getElementById('novaRadiusSlider');
        if (slider) { slider.value = m; }
        novaUpdateRadius(m);
    }

    function novaPinMode() {
        var LMap = novaTargetMap();
        if (!LMap) { novaShowError('No map found — switch to the Coverage Map tab first.'); return; }
        _novaPinning = !_novaPinning;
        var btn = document.getElementById('novaPinBtnTxt');
        if (_novaPinning) {
            if (btn) btn.textContent = 'Click on map…';
            LMap.getContainer().style.cursor = 'crosshair';
            LMap.once('click', function(e) {
                _novaPinning = false;
                if (btn) btn.textContent = 'Click map to pin';
                LMap.getContainer().style.cursor = '';
                _novaSetPin(e.latlng.lat, e.latlng.lng);
            });
        } else {
            if (btn) btn.textContent = 'Click map to pin';
            LMap.getContainer().style.cursor = '';
        }
    }

    function _novaSetPin(lat, lng) {
        _novaPinLat = lat;
        _novaPinLng = lng;
        var LMap = novaTargetMap();
        if (!LMap) return;

        if (_novaPinMarker) { LMap.removeLayer(_novaPinMarker); }
        _novaPinMarker = L.marker([lat, lng], {
            icon: L.divIcon({
                className: '',
                html: '<div style="width:28px;height:28px;background:#ef4444;border-radius:50% 50% 50% 0;transform:rotate(-45deg);border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.4);"></div>',
                iconSize: [28, 28],
                iconAnchor: [14, 28],
            })
        }).addTo(LMap).bindPopup('<b style="color:#dc2626;">Complaint location</b><br>' + lat.toFixed(5) + ', ' + lng.toFixed(5));

        var info = document.getElementById('novaPinInfo');
        var coords = document.getElementById('novaPinCoords');
        if (info) info.style.display = 'block';
        if (coords) coords.textContent = lat.toFixed(5) + ', ' + lng.toFixed(5);

        _novaDrawCircle(lat, lng, _novaRadiusM);
        LMap.setView([lat, lng], Math.max(LMap.getZoom(), 14));
    }

    function _novaDrawCircle(lat, lng, radiusM) {
        var LMap = novaTargetMap();
        if (!LMap) return;
        if (_novaCircleLayer) { LMap.removeLayer(_novaCircleLayer); }
        _novaCircleLayer = L.circle([lat, lng], {
            radius: radiusM,
            color: '#16a34a', weight: 2,
            fillColor: '#22c55e', fillOpacity: 0.08,
            dashArray: '6 4',
        }).addTo(LMap);
    }

    function novaClearPin() {
        var LMap = novaTargetMap();
        _novaPinLat = null; _novaPinLng = null;
        if (_novaPinMarker && LMap) { LMap.removeLayer(_novaPinMarker); _novaPinMarker = null; }
        if (_novaCircleLayer && LMap) { LMap.removeLayer(_novaCircleLayer); _novaCircleLayer = null; }
        var info = document.getElementById('novaPinInfo');
        if (info) info.style.display = 'none';
        clearNovaLayersJeak();
    }

    async function runNovaJeak() {
        if (_novaPinLat === null) {
            novaShowError('Pin a complaint location on the map first.');
            return;
        }
        var topK = parseInt(document.getElementById('novaTopK').value) || 3;
        var btn  = document.getElementById('novaRunBtn');
        var list = document.getElementById('novaCandidateList');
        var errEl = document.getElementById('novaError');
        if (errEl) errEl.style.display = 'none';
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Running NOVA…'; }
        if (list) list.innerHTML = '<div style="text-align:center;padding:24px;color:#16a34a;font-size:0.75rem;font-weight:700;"><i class="fas fa-circle-notch fa-spin" style="font-size:1.5rem;"></i><div style="margin-top:8px;">Generating candidates…</div></div>';

        try {
            var res = await fetch('/api/nova/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    complaint_lat:      _novaPinLat,
                    complaint_lng:      _novaPinLng,
                    radius_m:           _novaRadiusM,
                    top_k:              topK,
                    atom_cluster_np_id: window._novaAtomNpId || null,
                }),
            });
            var data = await res.json();
            if (!data.success) { novaShowError(data.error || 'Pipeline failed'); return; }
            novaRenderResultsJeak(data);
        } catch (e) {
            novaShowError(e.message || 'Network error');
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-play"></i> Run NOVA Analysis'; }
        }
    }

    window._novaAtomNpId = null;   // set when NOVA is triggered from an ATOM cluster

    function novaRenderResultsJeak(data) {
        _novaRunId = data.run_id || null;
        var LMap   = novaTargetMap();
        clearNovaLayersJeak();
        var meta = data.meta || {};

        // Stats bar
        var sb = document.getElementById('novaStatsBar');
        if (sb) sb.style.display = 'flex';
        var el = function(id, v) { var e = document.getElementById(id); if (e) e.textContent = v; };
        el('novaStatSites',      meta.n_sites      || 0);
        el('novaStatNPs',        meta.n_nps         || 0);
        el('novaStatCandidates', meta.n_candidates  || 0);

        // Geometry type badge + NP-id
        var geoType = data.geometry_type || 'city';
        var geoColor = geoType === 'highway' ? '#d97706' : '#2563eb';
        var npBadge = meta.atom_cluster_np_id
            ? '<span style="background:#dbeafe;color:#1d4ed8;font-family:monospace;font-weight:800;font-size:0.72rem;padding:2px 7px;border-radius:4px;">' + meta.atom_cluster_np_id + '</span>'
            : '';
        var chips = document.getElementById('novaParamChips');
        if (chips) chips.innerHTML =
            '<span style="display:inline-flex;align-items:center;gap:4px;background:rgba(255,255,255,.22);border:1px solid rgba(255,255,255,.35);border-radius:5px;padding:3px 8px;font-size:.68rem;font-weight:800;">r=' + _novaRadiusM + 'm</span>' +
            '<span style="background:' + geoColor + '44;color:white;border:1px solid ' + geoColor + ';border-radius:5px;padding:3px 8px;font-size:.68rem;font-weight:800;text-transform:uppercase;">' + geoType + '</span>' +
            (npBadge ? '<span style="background:rgba(255,255,255,.15);border-radius:5px;padding:3px 6px;">' + npBadge + '</span>' : '');

        var lt = document.getElementById('novaLayerToggles');
        if (lt) lt.style.display = 'flex';

        // Delaunay mesh
        if (data.delaunay_geojson && LMap) {
            _novaDelaunayLayer = L.geoJSON(data.delaunay_geojson, {
                style: { color: '#86efac', weight: 1, fillOpacity: 0.04, dashArray: '3 4' },
            }).addTo(LMap);
        }

        // Candidate markers + wedge rays
        if (data.geojson && LMap) {
            _novaCandidateLayer = L.geoJSON(data.geojson, {
                filter: function(f) {
                    return f.properties.type === 'candidate' || f.properties.type === 'candidate_ray';
                },
                style: function(f) {
                    if (f.properties.type === 'candidate_ray') {
                        return { color: f.properties.color, weight: 2, dashArray: '6 4', opacity: 0.7 };
                    }
                },
                pointToLayer: function(f, latlng) {
                    if (f.properties.type !== 'candidate') return null;
                    var p = f.properties;
                    // Determine wedge icon shape based on candidate type
                    var typeIcon = p.candidate_type === 'triangulation'
                        ? '<div style="font-size:0.5rem;margin-top:1px;opacity:.8;">⬡</div>'  // hexagon = equidistant
                        : '<div style="font-size:0.5rem;margin-top:1px;opacity:.8;">◉</div>'; // centroid
                    // Wedge sector pointing toward candidate from centroid
                    var bearing = p.bearing_deg || 0;
                    var wedgeHtml =
                        '<div style="position:relative;width:50px;height:50px;">' +
                        // Wedge sector (CSS clip-path)
                        '<div style="position:absolute;top:0;left:0;width:50px;height:50px;' +
                        'border:3px solid ' + p.color + ';border-radius:50%;opacity:0.35;"></div>' +
                        // Label circle
                        '<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);' +
                        'width:32px;height:32px;background:' + p.color + ';border-radius:50%;' +
                        'border:2.5px solid white;box-shadow:0 3px 10px rgba(0,0,0,0.4);' +
                        'display:flex;flex-direction:column;align-items:center;justify-content:center;' +
                        'font-size:0.82rem;font-weight:900;color:white;line-height:1;">' +
                        p.label + typeIcon + '</div>' +
                        // Direction arrow
                        '<div style="position:absolute;top:2px;left:50%;transform:translateX(-50%) rotate(' + bearing + 'deg);' +
                        'width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;' +
                        'border-bottom:10px solid ' + p.color + ';"></div>' +
                        '</div>';
                    return L.marker(latlng, {
                        icon: L.divIcon({ className: '', html: wedgeHtml, iconSize: [50, 50], iconAnchor: [25, 25] })
                    });
                },
                onEachFeature: function(f, layer) {
                    if (f.properties.type !== 'candidate') return;
                    var p = f.properties;
                    var rsrp = p.avg_rsrp !== null ? p.avg_rsrp + ' dBm' : 'N/A';
                    var typeLabel = p.candidate_type === 'triangulation'
                        ? '<span style="background:#dbeafe;color:#1d4ed8;border-radius:3px;padding:1px 5px;font-size:0.58rem;font-weight:800;">⬡ TRIANGULATION</span>'
                        : '<span style="background:#f0fdf4;color:#15803d;border-radius:3px;padding:1px 5px;font-size:0.58rem;font-weight:800;">◉ CENTROID</span>';
                    layer.bindPopup(
                        '<div style="font-family:Inter,sans-serif;min-width:200px;">' +
                        '<div style="background:' + p.color + ';color:white;padding:8px 12px;border-radius:8px 8px 0 0;font-weight:900;font-size:1rem;margin:-8px -8px 8px;">' +
                        'Candidate ' + p.label + (p.np_id ? ' <span style="font-size:0.62rem;font-weight:600;opacity:.85;">| ' + p.np_id + '</span>' : '') + '</div>' +
                        '<div style="margin-bottom:6px;">' + typeLabel + '</div>' +
                        '<table style="font-size:0.72rem;width:100%;border-collapse:collapse;">' +
                        '<tr><td style="color:#6b7280;padding:2px 0;">Rank</td><td style="font-weight:700;">#' + p.rank + '</td></tr>' +
                        '<tr><td style="color:#6b7280;padding:2px 0;">Distance</td><td style="font-weight:700;">' + p.dist_m + ' m</td></tr>' +
                        '<tr><td style="color:#6b7280;padding:2px 0;">Signal pts</td><td style="font-weight:700;">' + p.signal_count + '</td></tr>' +
                        '<tr><td style="color:#6b7280;padding:2px 0;">Weight score</td><td style="font-weight:700;">' + p.signal_weight_sum + '</td></tr>' +
                        '<tr><td style="color:#6b7280;padding:2px 0;">Avg RSRP</td><td style="font-weight:700;">' + rsrp + '</td></tr>' +
                        '</table>' +
                        (p.selection_reason ? '<div style="margin-top:6px;font-size:0.6rem;color:#15803d;background:#f0fdf4;border-radius:4px;padding:4px 6px;">✓ ' + p.selection_reason + '</div>' : '') +
                        '</div>'
                    );
                },
            }).addTo(LMap);
        }

        // Panel candidate list
        var list = document.getElementById('novaCandidateList');
        if (!list) return;
        var candidates = data.candidates || [];
        if (!candidates.length) {
            list.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:24px;font-size:0.75rem;">No candidates found. Try increasing radius.</div>';
            return;
        }

        // Geometry type notice
        var geoNotice = geoType === 'highway'
            ? '<div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:6px;padding:6px 10px;font-size:0.62rem;color:#92400e;margin-bottom:8px;"><strong>Highway geometry detected</strong> — centroid NPs used (equidistant circumcenter unreliable for linear site layouts).</div>'
            : '<div style="background:#dbeafe;border:1px solid #93c5fd;border-radius:6px;padding:6px 10px;font-size:0.62rem;color:#1e40af;margin-bottom:8px;"><strong>City geometry</strong> — triangulation circumcenters preferred (equidistant from surrounding sites).</div>';

        list.innerHTML = geoNotice + candidates.map(function(c) {
            var rsrp       = c.avg_rsrp !== null ? c.avg_rsrp + ' dBm' : '—';
            var typeLabel  = c.candidate_type === 'triangulation'
                ? '<span style="background:#dbeafe;color:#1d4ed8;border-radius:3px;padding:1px 5px;font-size:0.58rem;font-weight:800;">⬡ Triangulation</span>'
                : '<span style="background:#f0fdf4;color:#15803d;border-radius:3px;padding:1px 5px;font-size:0.58rem;font-weight:800;">◉ Centroid</span>';
            var sigBadge   = c.signal_count > 0
                ? '<span style="background:#fef3c7;color:#92400e;border-radius:4px;padding:1px 6px;font-size:0.58rem;font-weight:700;">' + c.signal_count + ' poor-sig pts</span>'
                : '<span style="background:#f1f5f9;color:#64748b;border-radius:4px;padding:1px 6px;font-size:0.58rem;">No signal data</span>';
            return '<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:8px;padding:10px 12px;margin-bottom:6px;border-left:4px solid ' + c.color + ';">' +
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;cursor:pointer;" onclick="novaFlyToCandidate(' + c.lat + ',' + c.lng + ')">' +
                '<div style="width:28px;height:28px;background:' + c.color + ';border-radius:50%;display:flex;align-items:center;justify-content:center;color:white;font-weight:900;font-size:0.82rem;flex-shrink:0;">' + c.label + '</div>' +
                '<div style="flex:1;">' +
                '<div style="font-size:0.75rem;font-weight:800;color:#166534;">Candidate ' + c.label +
                (c.np_id ? ' <span style="font-family:monospace;font-size:0.62rem;background:#f0fdf4;color:#15803d;padding:1px 5px;border-radius:3px;">' + c.np_id + '</span>' : '') +
                ' <span style="color:#6b7280;font-weight:400;font-size:0.62rem;">' + (c.dist_m || '?') + ' m</span></div>' +
                '<div style="margin-top:2px;">' + typeLabel + '</div>' +
                '<div style="font-size:0.6rem;color:#374151;margin-top:2px;">RSRP: ' + rsrp + ' · Score: ' + (c.signal_weight_sum || 0) + '</div>' +
                '</div></div>' +
                '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:5px;">' +
                sigBadge +
                '<button onclick="rolloutCreateFromNova(' + c.lat + ',' + c.lng + ',\'' + c.label + '\')" ' +
                'style="background:linear-gradient(135deg,#0c4a6e,#0369a1);color:white;border:none;border-radius:5px;padding:3px 9px;font-size:0.58rem;font-weight:700;cursor:pointer;display:flex;align-items:center;gap:4px;">' +
                '<i class="fas fa-route"></i> Rollout</button>' +
                '</div>' +
                '</div>';
        }).join('');
    }

    function novaFlyToCandidate(lat, lng) {
        var LMap = novaTargetMap();
        if (LMap) LMap.setView([lat, lng], Math.max(LMap.getZoom(), 15));
    }

    function toggleNovaDelaunayJeak() {
        var LMap = novaTargetMap();
        if (!_novaDelaunayLayer || !LMap) return;
        _novaDelaunayOn = !_novaDelaunayOn;
        if (_novaDelaunayOn) { LMap.addLayer(_novaDelaunayLayer); }
        else { LMap.removeLayer(_novaDelaunayLayer); }
        var btn = document.getElementById('novaToggleDelaunay');
        if (btn) btn.style.opacity = _novaDelaunayOn ? '1' : '0.4';
    }

    function toggleNovaCandidatesJeak() {
        var LMap = novaTargetMap();
        if (!_novaCandidateLayer || !LMap) return;
        _novaCandidatesOn = !_novaCandidatesOn;
        if (_novaCandidatesOn) { LMap.addLayer(_novaCandidateLayer); }
        else { LMap.removeLayer(_novaCandidateLayer); }
        var btn = document.getElementById('novaToggleCandidates');
        if (btn) btn.style.opacity = _novaCandidatesOn ? '1' : '0.4';
    }

    function clearNovaLayersJeak() {
        var LMap = novaTargetMap();
        if (LMap) {
            if (_novaDelaunayLayer)  { LMap.removeLayer(_novaDelaunayLayer);  _novaDelaunayLayer  = null; }
            if (_novaCandidateLayer) { LMap.removeLayer(_novaCandidateLayer); _novaCandidateLayer = null; }
        }
        var lt = document.getElementById('novaLayerToggles');
        if (lt) lt.style.display = 'none';
        var sb = document.getElementById('novaStatsBar');
        if (sb) sb.style.display = 'none';
        var list = document.getElementById('novaCandidateList');
        if (list) list.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:32px 16px;"><i class="fas fa-broadcast-tower" style="font-size:2rem;margin-bottom:8px;display:block;opacity:.4;"></i><p style="font-size:0.75rem;font-weight:600;color:#374151;">Pin a complaint, set radius, then run NOVA</p></div>';
    }

    function novaShowError(msg) {
        var errEl = document.getElementById('novaError');
        if (errEl) { errEl.textContent = '⚠ ' + (msg || 'Error'); errEl.style.display = 'block'; }
        var list = document.getElementById('novaCandidateList');
        if (list) list.innerHTML = '<div style="text-align:center;color:#dc2626;padding:24px;font-size:0.75rem;font-weight:600;"><i class="fas fa-exclamation-triangle"></i> ' + String(msg || 'Error') + '</div>';
    }

    async function loadNovaHistoryJeak() {
        try {
            var res = await fetch('/api/nova/history');
            var runs = await res.json();
            var list = document.getElementById('novaCandidateList');
            if (!runs.length) {
                if (list) list.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:24px;font-size:0.75rem;">No NOVA runs saved yet.</div>';
                return;
            }
            if (list) list.innerHTML =
                '<div style="font-size:0.65rem;font-weight:800;color:#374151;text-transform:uppercase;margin-bottom:8px;padding:0 2px;">Past Runs</div>' +
                runs.map(function(r) {
                    var d = r.ran_at ? r.ran_at.replace('T',' ').slice(0,16) : '—';
                    return '<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:8px 10px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;">' +
                        '<div>' +
                        '<div style="font-size:0.72rem;font-weight:700;color:#166534;">' + d + '</div>' +
                        '<div style="font-size:0.62rem;color:#6b7280;">r=' + r.radius_m + 'm · ' + r.n_candidates + ' candidates · by ' + r.initiated_by + '</div>' +
                        '</div>' +
                        '<button onclick="novaReloadRun(' + r.id + ',' + r.complaint_lat + ',' + r.complaint_lng + ',' + r.radius_m + ')" ' +
                        'style="font-size:0.62rem;font-weight:700;color:#15803d;background:#f0fdf4;border:1px solid #86efac;border-radius:6px;padding:4px 8px;cursor:pointer;">Reload</button>' +
                        '</div>';
                }).join('');
        } catch (err) { novaShowError('Could not load history: ' + err.message); }
    }

    // ═══════════════════════════════════════════════════════════════
    //  PAVE — Path & Visibility Evaluation
    // ═══════════════════════════════════════════════════════════════

    var _paveOpen = false;
    var _paveLat  = null, _paveLng  = null;
    var _paveRunId = null;
    var _pavePinning = false;
    var _pavePinMarker   = null;
    var _paveViewshedLayer = null;
    var _paveLinesLayer    = null;
    var _paveViewshedOn    = true;
    var _paveLinesOn       = true;
    var _paveSitesData     = [];
    var _paveDem           = null;

    function togglePavePanel() {
        var p = document.getElementById('pavePanel');
        if (!p) return;
        _paveOpen = !_paveOpen;
        if (_paveOpen) p.classList.add('open');
        else           p.classList.remove('open');
    }

    function paveTargetMap() {
        if (typeof mhMap !== 'undefined' && mhMap) return mhMap;
        if (typeof coverageMap !== 'undefined' && coverageMap) return coverageMap;
        if (typeof map !== 'undefined' && map) return map;
        var containers = document.querySelectorAll('.leaflet-container');
        for (var i = 0; i < containers.length; i++) {
            var m = containers[i]._leaflet_map;
            if (m) return m;
        }
        return null;
    }

    function pavePinMode() {
        var LMap = paveTargetMap();
        if (!LMap) { paveShowError('No map found — switch to Coverage Map tab first.'); return; }
        _pavePinning = !_pavePinning;
        var btn = document.getElementById('pavePinBtnTxt');
        if (_pavePinning) {
            if (btn) btn.textContent = 'Click on map…';
            LMap.getContainer().style.cursor = 'crosshair';
            LMap.once('click', function(e) {
                _pavePinning = false;
                if (btn) btn.textContent = 'Pin manually';
                LMap.getContainer().style.cursor = '';
                _paveSetPin(e.latlng.lat, e.latlng.lng, 'Manual pin');
            });
        } else {
            if (btn) btn.textContent = 'Pin manually';
            LMap.getContainer().style.cursor = '';
        }
    }

    function paveUseNova() {
        // Populate picker from NOVA candidates on map
        var picker = document.getElementById('paveNovaPicker');
        if (!picker) return;
        if (!_novaCandidateLayer) {
            paveShowError('Run NOVA first to get candidates.');
            return;
        }
        picker.innerHTML = '';
        picker.style.display = 'flex';
        var added = 0;
        _novaCandidateLayer.eachLayer(function(lyr) {
            var p = lyr.feature && lyr.feature.properties;
            if (!p || p.type !== 'candidate') return;
            var ll = lyr.getLatLng();
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.style.cssText = 'background:' + p.color + ';color:white;border:none;border-radius:6px;padding:4px 12px;font-size:0.7rem;font-weight:800;cursor:pointer;';
            btn.textContent = 'Candidate ' + p.label;
            btn.onclick = (function(lat, lng, label) {
                return function() { _paveSetPin(lat, lng, 'NOVA Candidate ' + label); };
            })(ll.lat, ll.lng, p.label);
            picker.appendChild(btn);
            added++;
        });
        if (!added) paveShowError('No NOVA candidates on map. Run NOVA first.');
    }

    function _paveSetPin(lat, lng, label) {
        _paveLat = lat; _paveLng = lng;
        var LMap = paveTargetMap();
        if (_pavePinMarker && LMap) { LMap.removeLayer(_pavePinMarker); }
        if (LMap) {
            _pavePinMarker = L.marker([lat, lng], {
                icon: L.divIcon({
                    className: '',
                    html: '<div style="width:30px;height:30px;background:#d97706;border-radius:50%;border:3px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;"><i class="fas fa-eye" style="color:white;font-size:0.7rem;"></i></div>',
                    iconSize: [30, 30], iconAnchor: [15, 15],
                })
            }).addTo(LMap).bindPopup('<b style="color:#92400e;">PAVE candidate</b><br>' + label + '<br>' + lat.toFixed(5) + ', ' + lng.toFixed(5));
            LMap.setView([lat, lng], Math.max(LMap.getZoom(), 13));
        }
        var info = document.getElementById('pavePinInfo');
        var coords = document.getElementById('pavePinCoords');
        if (info) info.style.display = 'block';
        if (coords) coords.textContent = label + ' — ' + lat.toFixed(5) + ', ' + lng.toFixed(5);
        var errEl = document.getElementById('paveError');
        if (errEl) errEl.style.display = 'none';
    }

    async function runPaveJeak() {
        if (_paveLat === null) { paveShowError('Select or pin a candidate location first.'); return; }
        var btn  = document.getElementById('paveRunBtn');
        var list = document.getElementById('paveSiteList');
        var errEl = document.getElementById('paveError');
        if (errEl) errEl.style.display = 'none';
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Running PAVE…'; }
        if (list) list.innerHTML = '<div style="text-align:center;padding:24px;color:#d97706;font-size:0.75rem;font-weight:700;"><i class="fas fa-circle-notch fa-spin" style="font-size:1.5rem;"></i><div style="margin-top:8px;">Computing viewshed + LOS…</div><div style="font-size:0.65rem;color:#9ca3af;margin-top:4px;">This may take 15–60 seconds</div></div>';
        document.getElementById('paveProfileBox').style.display = 'none';

        try {
            var body = { candidate_lat: _paveLat, candidate_lon: _paveLng };
            var res  = await fetch('/api/pave/run', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
            var data = await res.json();
            if (!data.success) { paveShowError(data.error || 'Pipeline failed'); return; }
            paveRenderResultsJeak(data);
        } catch (e) {
            paveShowError(e.message || 'Network error');
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-satellite-dish"></i> Run PAVE Analysis'; }
        }
    }

    function paveRenderResultsJeak(data) {
        var LMap = paveTargetMap();
        clearPaveLayersJeak();
        _paveRunId = data.run_id || null;
        _paveSitesData = data.sites || [];
        var summary = data.summary || {};

        // Stats bar
        var sb = document.getElementById('paveStatsBar');
        if (sb) sb.style.display = 'flex';
        document.getElementById('paveStatNearby').textContent = summary.total_nearby || 0;
        document.getElementById('paveStatLOS').textContent    = summary.los_count    || 0;
        document.getElementById('paveStatNoLOS').textContent  = summary.no_los_count || 0;
        document.getElementById('paveStatTime').textContent   = (summary.processing_time_s || '—');

        // Param chips
        var chips = document.getElementById('paveParamChips');
        if (chips) chips.innerHTML =
            '<span style="display:inline-flex;align-items:center;gap:3px;background:rgba(255,255,255,.22);border:1px solid rgba(255,255,255,.35);border-radius:5px;padding:2px 7px;font-size:.65rem;font-weight:800;">' +
            (summary.los_count||0) + ' LOS / ' + (summary.total_nearby||0) + ' sites</span>';

        // Layer toggles
        var lt = document.getElementById('paveLayerToggles');
        if (lt) lt.style.display = 'flex';

        // Viewshed polygon
        if (data.viewshed_geojson && LMap) {
            _paveViewshedLayer = L.geoJSON(
                { type: 'Feature', geometry: data.viewshed_geojson, properties: {} },
                { style: { color: '#d97706', weight: 1.5, fillColor: '#fbbf24', fillOpacity: 0.12 } }
            ).addTo(LMap);
        }

        // LOS lines
        if (_paveSitesData.length && LMap) {
            var lineFeatures = _paveSitesData.map(function(s) {
                return {
                    type: 'Feature',
                    geometry: { type: 'LineString', coordinates: [[_paveLng, _paveLat], [s.lng, s.lat]] },
                    properties: { los: s.los, site_id: s.site_id, distance_m: s.distance_m },
                };
            });
            _paveLinesLayer = L.geoJSON(
                { type: 'FeatureCollection', features: lineFeatures },
                {
                    style: function(f) {
                        return f.properties.los
                            ? { color: '#16a34a', weight: 1.5, opacity: 0.7 }
                            : { color: '#dc2626', weight: 1.5, opacity: 0.5, dashArray: '4 4' };
                    },
                    onEachFeature: function(f, layer) {
                        layer.bindTooltip(f.properties.site_id + ' — ' +
                            (f.properties.los ? '✓ LOS' : '✗ No LOS') + ' · ' + f.properties.distance_m + 'm');
                    },
                }
            ).addTo(LMap);
        }

        // Site list
        paveBuildSiteList(_paveSitesData);
    }

    function paveBuildSiteList(sites) {
        var list = document.getElementById('paveSiteList');
        if (!list) return;
        if (!sites.length) {
            list.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:24px;font-size:0.75rem;">No nearby sites found.</div>';
            return;
        }
        var los  = sites.filter(function(s) { return s.los; });
        var nlos = sites.filter(function(s) { return !s.los; });

        function siteCard(s, idx) {
            var col  = s.los ? '#16a34a' : '#dc2626';
            var icon = s.los ? '✓' : '✗';
            var bg   = s.los ? '#f0fdf4' : '#fef2f2';
            var bdr  = s.los ? '#86efac' : '#fca5a5';
            return '<div style="background:' + bg + ';border:1px solid ' + bdr + ';border-radius:7px;padding:7px 10px;margin-bottom:5px;cursor:pointer;border-left:4px solid ' + col + ';" onclick="paveShowProfile(' + idx + ')">' +
                '<div style="display:flex;justify-content:space-between;align-items:center;">' +
                '<span style="font-size:0.72rem;font-weight:800;color:#1e293b;">' + s.site_id + '</span>' +
                '<span style="font-size:0.7rem;font-weight:900;color:' + col + ';">' + icon + ' ' + (s.los ? 'LOS' : 'No LOS') + '</span>' +
                '</div>' +
                '<div style="font-size:0.62rem;color:#6b7280;margin-top:2px;">' + s.distance_m + ' m away · click for profile</div>' +
                '</div>';
        }

        var html = '';
        if (los.length) {
            html += '<div style="font-size:0.6rem;font-weight:800;color:#16a34a;text-transform:uppercase;margin:6px 0 4px;padding:0 2px;">✓ Clear LOS (' + los.length + ')</div>';
            los.forEach(function(s) { html += siteCard(s, sites.indexOf(s)); });
        }
        if (nlos.length) {
            html += '<div style="font-size:0.6rem;font-weight:800;color:#dc2626;text-transform:uppercase;margin:10px 0 4px;padding:0 2px;">✗ Blocked (' + nlos.length + ')</div>';
            nlos.forEach(function(s) { html += siteCard(s, sites.indexOf(s)); });
        }
        list.innerHTML = html;
    }

    async function paveShowProfile(idx) {
        var s = _paveSitesData[idx];
        if (!s) return;
        var box   = document.getElementById('paveProfileBox');
        var title = document.getElementById('paveProfileTitle');
        var chart = document.getElementById('paveProfileChart');
        if (title) title.textContent = s.site_id + ' — ' + (s.los ? '✓ LOS Clear' : '✗ LOS Blocked') + ' · ' + s.distance_m + 'm';
        box.style.display = 'block';

        // Only fetch if profile not already in memory
        if (!s.profile) {
            if (chart) chart.innerHTML = '<div style="text-align:center;padding:20px;color:#d97706;font-size:0.75rem;"><i class="fas fa-circle-notch fa-spin"></i> Loading profile…</div>';
            try {
                var res = await fetch('/api/pave/profile', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        candidate_lat: _paveLat,
                        candidate_lon: _paveLng,
                        site_lat: s.lat,
                        site_lng: s.lng,
                        site_id: s.site_id,
                        run_id: _paveRunId,
                    }),
                });
                var data = await res.json();
                if (!data.success) { if (chart) chart.innerHTML = '<div style="color:#dc2626;padding:10px;font-size:0.72rem;">Profile failed: ' + data.error + '</div>'; return; }
                s.profile = data.profile;   // cache in memory
            } catch(e) {
                if (chart) chart.innerHTML = '<div style="color:#dc2626;padding:10px;font-size:0.72rem;">Network error</div>';
                return;
            }
        }

        var p = s.profile;

        var col = s.los ? '#16a34a' : '#dc2626';
        Plotly.react('paveProfileChart', [
            {
                x: p.distances_m,
                y: p.terrain_corrected,
                type: 'scatter', mode: 'lines',
                fill: 'tozeroy', fillcolor: 'rgba(120,113,108,0.25)',
                line: { color: '#78716c', width: 1.5 },
                name: 'Terrain',
            },
            {
                x: p.distances_m,
                y: p.los_line,
                type: 'scatter', mode: 'lines',
                line: { color: col, width: 2, dash: s.los ? 'solid' : 'dash' },
                name: 'LOS line',
            },
        ], {
            margin: { t: 4, b: 28, l: 36, r: 8 },
            height: 130,
            paper_bgcolor: 'transparent',
            plot_bgcolor:  'rgba(255,255,255,0.5)',
            font: { size: 9, family: 'Inter,sans-serif' },
            xaxis: { title: 'Distance (m)', tickfont: { size: 8 } },
            yaxis: { title: 'Elev (m)', tickfont: { size: 8 } },
            legend: { orientation: 'h', y: -0.35, font: { size: 8 } },
            showlegend: true,
        }, { displayModeBar: false, responsive: true });
    }

    function togglePaveViewshedJeak() {
        var LMap = paveTargetMap();
        if (!_paveViewshedLayer || !LMap) return;
        _paveViewshedOn = !_paveViewshedOn;
        if (_paveViewshedOn) { LMap.addLayer(_paveViewshedLayer); }
        else { LMap.removeLayer(_paveViewshedLayer); }
        var btn = document.getElementById('paveToggleViewshed');
        if (btn) btn.style.opacity = _paveViewshedOn ? '1' : '0.4';
    }

    function togglePaveLinesJeak() {
        var LMap = paveTargetMap();
        if (!_paveLinesLayer || !LMap) return;
        _paveLinesOn = !_paveLinesOn;
        if (_paveLinesOn) { LMap.addLayer(_paveLinesLayer); }
        else { LMap.removeLayer(_paveLinesLayer); }
        var btn = document.getElementById('paveToggleLines');
        if (btn) btn.style.opacity = _paveLinesOn ? '1' : '0.4';
    }

    function clearPaveLayersJeak() {
        var LMap = paveTargetMap();
        if (LMap) {
            if (_paveViewshedLayer) { LMap.removeLayer(_paveViewshedLayer); _paveViewshedLayer = null; }
            if (_paveLinesLayer)    { LMap.removeLayer(_paveLinesLayer);    _paveLinesLayer    = null; }
        }
        var lt = document.getElementById('paveLayerToggles');
        if (lt) lt.style.display = 'none';
        var sb = document.getElementById('paveStatsBar');
        if (sb) sb.style.display = 'none';
        document.getElementById('paveProfileBox').style.display = 'none';
        var list = document.getElementById('paveSiteList');
        if (list) list.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:28px;"><i class="fas fa-eye" style="font-size:2rem;display:block;opacity:.4;margin-bottom:8px;"></i><p style="font-size:0.75rem;color:#374151;font-weight:600;">Select a candidate, then run PAVE</p></div>';
    }

    function paveShowError(msg) {
        var errEl = document.getElementById('paveError');
        if (errEl) { errEl.textContent = '⚠ ' + (msg || 'Error'); errEl.style.display = 'block'; }
        var list = document.getElementById('paveSiteList');
        if (list) list.innerHTML = '<div style="text-align:center;color:#dc2626;padding:24px;font-size:0.75rem;font-weight:600;"><i class="fas fa-exclamation-triangle"></i> ' + String(msg || 'Error') + '</div>';
    }

    async function loadPaveHistoryJeak() {
        try {
            var res  = await fetch('/api/pave/history');
            var runs = await res.json();
            var list = document.getElementById('paveSiteList');
            if (!runs.length) {
                if (list) list.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:24px;font-size:0.75rem;">No PAVE runs saved yet.</div>';
                return;
            }
            if (list) list.innerHTML =
                '<div style="font-size:0.65rem;font-weight:800;color:#374151;text-transform:uppercase;margin-bottom:8px;">Past Runs</div>' +
                runs.map(function(r) {
                    var d = r.ran_at ? r.ran_at.replace('T',' ').slice(0,16) : '—';
                    return '<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:8px 10px;margin-bottom:6px;">' +
                        '<div style="font-size:0.72rem;font-weight:700;color:#92400e;">' + d + (r.nova_candidate_label ? ' · Candidate ' + r.nova_candidate_label : '') + '</div>' +
                        '<div style="font-size:0.62rem;color:#6b7280;">' + r.los_count + ' LOS / ' + r.total_nearby + ' sites · ' + r.processing_time_s + 's · by ' + r.initiated_by + '</div>' +
                        '</div>';
                }).join('');
        } catch (err) { paveShowError('Could not load history: ' + err.message); }
    }

    // PAVE panel resize
    (function initPavePanelResizeJeak() {
        var handle = document.getElementById('paveResizeHandle'), panel = document.getElementById('pavePanel');
        if (!handle || !panel) return;
        var drag = false, sx = 0, sw = 0;
        handle.addEventListener('mousedown', function(ev) {
            drag = true; sx = ev.clientX; sw = panel.offsetWidth; handle.classList.add('dragging'); ev.preventDefault();
        });
        document.addEventListener('mousemove', function(ev) {
            if (!drag) return;
            panel.style.width = Math.max(320, Math.min(620, sw - (ev.clientX - sx))) + 'px';
        });
        document.addEventListener('mouseup', function() { drag = false; handle.classList.remove('dragging'); });
    })();

    async function novaReloadRun(runId, lat, lng, radiusM) {
        try {
            var res = await fetch('/api/nova/run/' + runId);
            var data = await res.json();
            if (!data.success || !data.candidates.length) { novaShowError('No saved candidates for run #' + runId); return; }
            // Restore pin + circle
            _novaSetPin(lat, lng);
            novaSetRadius(radiusM);
            // Build minimal result object to reuse renderResults
            novaRenderResultsJeak({
                candidates: data.candidates,
                geojson: {
                    type: 'FeatureCollection',
                    features: data.candidates.map(function(c) {
                        return { type: 'Feature',
                            geometry: { type: 'Point', coordinates: [c.lng, c.lat] },
                            properties: Object.assign({ type: 'candidate' }, c) };
                    })
                },
                delaunay_geojson: { type: 'FeatureCollection', features: [] },
                meta: { n_sites: '—', n_nps: '—', n_candidates: data.candidates.length },
            });
        } catch (err) { novaShowError('Reload failed: ' + err.message); }
    }



