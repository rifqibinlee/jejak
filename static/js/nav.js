        // Helper to set a nav button active/inactive with the new corporate inline styling
        function _navSetActive(btnId, active) {
            const btn = document.getElementById(btnId);
            if (!btn) return;
            if (active) {
                btn.style.color = '#0f2d52';
                btn.style.background = '#e2e8f0';
                btn.classList.add('nav-active');
            } else {
                btn.style.color = '#64748b';
                btn.style.background = 'transparent';
                btn.classList.remove('nav-active');
            }
        }

        function switchMainTab(tab) {
            const dashboardView  = document.getElementById('dashboardView');
            const mapContainer   = document.getElementById('mapContainer');
            const map3dContainer = document.getElementById('map3dContainer');
            const cctvContainer  = document.getElementById('cctvContainer');
            const bitcoinContainer = document.getElementById('bitcoinContainer');
            const gensetContainer  = document.getElementById('gensetContainer');
            const pegmanControl    = document.getElementById('pegmanControl');

            // Hide all views
            if (dashboardView)   dashboardView.style.display   = 'none';
            if (mapContainer)    mapContainer.style.display    = 'none';
            if (map3dContainer)  map3dContainer.style.display  = 'none';
            if (cctvContainer)   cctvContainer.style.display   = 'none';
            if (bitcoinContainer) bitcoinContainer.style.display = 'none';
            if (gensetContainer)  gensetContainer.style.display  = 'none';

            // Reset all nav buttons
            ['tabBtnDashboard','tabBtnMap','tabBtnTools','tabBtnOps'].forEach(id => _navSetActive(id, false));

            // Hide pegman by default
            if (pegmanControl) pegmanControl.style.visibility = 'hidden';

            // Close dropdowns
            document.getElementById('toolsDropdown')?.classList.remove('open');
            document.getElementById('opsDropdown')?.classList.remove('open');

            // Close sliding panels unless on a map-based tab
            if (tab !== 'map' && tab !== 'genset' && tab !== 'cctv' && tab !== 'bitcoin') {
                ['annotationPanel','tasksPanel','notesPanel'].forEach(id => {
                    document.getElementById(id)?.classList.remove('open');
                });
                // Reset button active states
                document.getElementById('annotationBtn')?.classList.remove('active');
                document.getElementById('notesBtn')?.classList.remove('active');
                if (typeof annPanelOpen !== 'undefined') annPanelOpen = false;
                if (typeof cancelDraw === 'function') try { cancelDraw(); } catch(e) {}
            }

            if (tab === 'dashboard') {
                if (dashboardView) { dashboardView.style.display = 'block'; setTimeout(function(){ if(window._initRanGlobe) window._initRanGlobe(); }, 60); }
                _navSetActive('tabBtnDashboard', true);
                setTimeout(() => { window.dispatchEvent(new Event('resize')); }, 100);

            } else if (tab === 'map') {
                if (mapContainer) mapContainer.style.display = 'block';
                if (pegmanControl) pegmanControl.style.visibility = 'visible';
                _navSetActive('tabBtnMap', true);
                setTimeout(() => { if (typeof initMapIfNeeded === 'function') initMapIfNeeded(); }, 200);

            } else if (tab === '3d') {
                if (map3dContainer) map3dContainer.style.display = 'block';
                setTimeout(() => { if (typeof initCesiumIfNeeded === 'function') initCesiumIfNeeded(); }, 200);

            } else if (tab === 'cctv') {
                if (cctvContainer) cctvContainer.style.display = 'block';
                _navSetActive('tabBtnTools', true);
                setTimeout(() => { if (typeof initCctvMap === 'function') initCctvMap(); }, 200);

            } else if (tab === 'bitcoin') {
                if (bitcoinContainer) bitcoinContainer.style.display = 'block';
                _navSetActive('tabBtnTools', true);
                setTimeout(() => { if (typeof initMinerHunter === 'function') initMinerHunter(); }, 200);

            } else if (tab === 'genset') {
                if (gensetContainer) gensetContainer.style.display = 'block';
                _navSetActive('tabBtnOps', true);
                setTimeout(() => { if (typeof initGensetMap === 'function') initGensetMap(); }, 200);
            }
        }

        function toggleToolsDropdown() {
            const dd = document.getElementById('toolsDropdown');
            const opsdd = document.getElementById('opsDropdown');
            opsdd.classList.remove('open');
            dd.classList.toggle('open');
        }

        function toggleOpsDropdown() {
            const dd = document.getElementById('opsDropdown');
            const toolsdd = document.getElementById('toolsDropdown');
            toolsdd.classList.remove('open');
            dd.classList.toggle('open');
        }

        // Close dropdowns when clicking outside
        document.addEventListener('click', function(e) {
            const wrapper = document.getElementById('toolsDropdownWrapper');
            const dd = document.getElementById('toolsDropdown');
            if (wrapper && dd && !wrapper.contains(e.target)) {
                dd.classList.remove('open');
            }
            const opsWrapper = document.getElementById('opsDropdownWrapper');
            const opsdd = document.getElementById('opsDropdown');
            if (opsWrapper && opsdd && !opsWrapper.contains(e.target)) {
                opsdd.classList.remove('open');
            }
            // Close user menu
            const menuBtn = document.getElementById('menuBtn');
            const menuDd  = document.getElementById('menuDropdown');
            if (menuBtn && menuDd && !menuBtn.contains(e.target) && !menuDd.contains(e.target)) {
                menuDd.classList.remove('open');
            }
        });

    // --- DASHBOARD CONTROLLER & DATA LOGIC ---
        const Dashboard = {
            state: { region: 'All', operator: 'All', cluster: 'All', year: new Date().getFullYear(), week: 'All' },
            init: async function() {
                await this.populateYears();
                await this.populateFilters();
                this.bindEvents();
                this.updateScorecards();
            },
            populateYears: async function() {
                try {
                    const res = await fetch('/api/years');
                    const years = await res.json() || [];
                    const sel = document.getElementById('globalFilterYear');
                    if (!sel) return;
                    sel.innerHTML = '';
                    if (years.length > 0) {
                        years.forEach(y => { const opt = document.createElement('option'); opt.value = y; opt.textContent = y; sel.appendChild(opt); });
                        this.state.year = years[0]; sel.value = this.state.year;
                    } else { sel.innerHTML = `<option value="${this.state.year}">${this.state.year}</option>`; }
                } catch(e) {}
            },
            populateFilters: async function() {
                try {
                    const res = await fetch('/api/filters/regions');
                    const regions = await res.json() || [];
                    const sel = document.getElementById('globalFilterRegion');
                    if (sel) {
                        sel.innerHTML = '<option value="All">All Regions</option>';
                        regions.forEach(r => { const opt = document.createElement('option'); opt.value = r; opt.textContent = r; sel.appendChild(opt); });
                    }
                    
                    // --- CHANGED: Fetch global timeline and format as YYYY-WW ---
                    const resW = await fetch('/api/weeks'); 
                    const weeks = await resW.json() || [];
                    const selW = document.getElementById('globalFilterWeek');
                    if (selW) {
                        selW.innerHTML = '<option value="All">All Weeks</option>';
                        weeks.forEach(w => { 
                            const opt = document.createElement('option'); 
                            opt.value = `${w.year}-${w.week}`; 
                            opt.textContent = `Week ${w.week} (${w.year})`; 
                            selW.appendChild(opt); 
                        });
                    }
                } catch(e) {}
            },
            bindEvents: function() {
                $('#globalFilterYear').on('change', async (e) => { this.state.year = e.target.value; this.refreshAll(); });
                $('#globalFilterRegion').on('change', (e) => { this.state.region = e.target.value; this.refreshAll(); });
                $('#globalFilterOperator').on('change', (e) => { this.state.operator = e.target.value; this.refreshAll(); });
                $('#globalFilterCluster').on('input', (e) => { this.state.cluster = e.target.value || 'All'; this.refreshAll(); });
                
                // --- CHANGED: Split the YYYY-WW format into separate state variables ---
                $('#globalFilterWeek').on('change', (e) => {
                    const val = e.target.value;
                    if (val !== 'All') {
                        const parts = val.split('-');
                        this.state.year = parts[0];
                        this.state.week = parts[1];
                        document.getElementById('globalFilterYear').value = parts[0]; // Sync year dropdown
                    } else {
                        this.state.week = 'All';
                    }
                    this.refreshAll();
                });
            },
            refreshAll: function() {
                if ($.fn.DataTable.isDataTable('#sectorTable')) $('#sectorTable').DataTable().ajax.reload(null, false);
                if ($.fn.DataTable.isDataTable('#forecastTable')) $('#forecastTable').DataTable().ajax.reload(null, false);
                if ($.fn.DataTable.isDataTable('#congestionTable')) $('#congestionTable').DataTable().ajax.reload(null, false);
                const params = new URLSearchParams(this.state).toString();
                if(document.getElementById('btnDownloadSector')) document.getElementById('btnDownloadSector').href = `/download/sector?${params}`;
                if(document.getElementById('btnDownloadForecast')) document.getElementById('btnDownloadForecast').href = `/download/forecast?${params}`;
                if(document.getElementById('btnDownloadCongested')) document.getElementById('btnDownloadCongested').href = `/download/congested?${params}`;
                this.updateScorecards();
            },
            updateScorecards: async function() {
                try {
                    const params = new URLSearchParams(this.state).toString();
                    const res = await fetch(`/api/dashboard/stats?${params}&_t=${Date.now()}`);
                    const data = await res.json();
                    if (document.getElementById('scoreTotalSectors')) document.getElementById('scoreTotalSectors').innerText = (data.total_sectors || 0).toLocaleString();
                    if (document.getElementById('scoreCongested')) document.getElementById('scoreCongested').innerText = (data.congested_count || 0).toLocaleString();
                    if (document.getElementById('scoreAvgVol')) document.getElementById('scoreAvgVol').innerText = (data.avg_volume || 0).toFixed(1);
                } catch(e) {}
            }
        };

        async function loadResults() {
            document.getElementById('resultsSection').classList.remove('hidden');
            await Dashboard.init(); // This finds the year
            
            // 🚨 Ensure the year is explicitly set in the state before tables load
            if (Dashboard.state.year === undefined) {
                Dashboard.state.year = document.getElementById('globalFilterYear').value;
            }

            if ($.fn.DataTable.isDataTable('#sectorTable')) { 
                Dashboard.refreshAll(); 
            } else { 
                displaySectorData(); 
                displayForecastData(); 
                displayCongestionData(); 
                autoSelectTopSite(); 
            }
        }
        
        window.addEventListener('DOMContentLoaded', function() { loadResults(); });

        async function autoSelectTopSite() {
            try {
                const week = Dashboard.state.week === 'All' ? '' : Dashboard.state.week;
                const res = await fetch(`/api/map/top_congested?week=${week}`);
                const data = await res.json();
                if (data && data.length > 0) {
                    const targetSite = data[0].zoom_sector_id.split('_')[0].split('-')[0];
                    document.getElementById('plotSiteInput').value = targetSite;
                    document.getElementById('generatePlotBtn').click();
                }
            } catch (e) {}
        }

        function displaySectorData() {
            $('#sectorTable').DataTable({
                serverSide: true, processing: true, scrollX: true, dom: 'frtip',
                ajax: { url: '/api/sector_data', data: function(d) { return $.extend({}, d, Dashboard.state); }, dataSrc: 'data' },
                columns: [
                    { title: 'Sector ID', data: d => d.zoom_sector_id_override || d.zoom_sector_id || '-' },
                    { title: 'Operator', data: 'operator', render: d => d === 'Celcom' ? '<span class="px-2 py-1 rounded bg-blue-100 text-blue-800 text-xs font-bold">Celcom</span>' : (d === 'Digi' ? '<span class="px-2 py-1 rounded bg-yellow-100 text-yellow-800 text-xs font-bold">Digi</span>' : '-') },
                    { title: 'Week', data: 'week' }, { title: 'Region', data: 'region' },
                    { title: 'Layers', data: 'f1f2f3', render: d => d ? `<span class="font-mono text-xs font-bold text-gray-600 tracking-wider">${d.toUpperCase()}</span>` : '-' },
                    { title: 'Area Target', data: 'area_target', render: d => { if (!d) return '-'; if (d.toLowerCase().includes('urban')) return `<span class="px-2 py-1 rounded bg-purple-100 text-purple-800 text-xs font-bold border border-purple-200">${d}</span>`; if (d.toLowerCase().includes('outside')) return `<span class="px-2 py-1 rounded bg-teal-100 text-teal-800 text-xs font-bold border border-teal-200">${d}</span>`; return `<span class="text-xs font-semibold">${d}</span>`; } },
                    { title: 'PRB Util (%)', data: 'eric_prb_util_rate', render: d => d != null ? parseFloat(d).toFixed(2) : '-' },
                    { title: 'DL Thpt', data: 'eric_dl_user_ip_thpt', render: d => d != null ? parseFloat(d).toFixed(2) : '-' },
                    { title: 'Vol (GB)', data: 'eric_data_volume_ul_dl', render: d => d != null ? parseFloat(d).toFixed(2) : '-' }
                ], pageLength: 25
            });
            $('#sectorTable tbody').on('click', 'tr', function () {
                var data = $('#sectorTable').DataTable().row(this).data();
                if (data) {
                    document.getElementById('plotSiteInput').value = (data.zoom_sector_id_override || data.zoom_sector_id).split('_')[0];
                    document.getElementById('generatePlotBtn').click();
                    document.getElementById('plotSection').scrollIntoView({behavior:'smooth'});
                }
            });
        }

        function displayForecastData() {
            $('#forecastTable').DataTable({
                serverSide: true, processing: true, scrollX: true, dom: 'Bfrtip',
                buttons: [{ extend: 'csvHtml5', className: 'hidden', filename: 'VIBE_Forecast_Export_' + Dashboard.state.year }],
                ajax: { url: '/api/forecast_data', data: function(d) { return $.extend({}, d, Dashboard.state); }, dataSrc: 'data' },
                columns: [
                    { title: 'Sector ID', data: d => d.zoom_sector_id || '-' },
                    { title: 'Operator', data: 'operator', render: d => d === 'Celcom' ? '<span class="px-2 py-1 rounded bg-blue-100 text-blue-800 text-xs font-bold">Celcom</span>' : (d === 'Digi' ? '<span class="px-2 py-1 rounded bg-yellow-100 text-yellow-800 text-xs font-bold">Digi</span>' : '-') },
                    { title: 'Year', data: 'year' }, { title: 'Week', data: 'week' }, { title: 'Month', data: 'month' },
                    { title: 'Actual Vol (GB)', data: 'actual_data_volume', render: d => d ? `<span class="actual-cell">${Number(d).toFixed(2)}</span>` : '-' },
                    { title: 'Pred Vol (GB)', data: 'predicted_eric_data_volume_ul_dl', render: d => d ? `<span class="pred-cell">${Number(d).toFixed(2)}</span>` : '-' },
                    { title: 'Actual PRB (%)', data: 'actual_prb_util_rate', render: d => d ? `<span class="actual-cell">${Number(d).toFixed(2)}</span>` : '-' },
                    { title: 'Pred PRB (%)', data: 'predicted_eric_prb_util_rate', render: d => d ? `<span class="pred-cell">${Number(d).toFixed(2)}</span>` : '-' },
                    { title: 'Actual DL Thpt (Mbps)', data: 'actual_dl_user_ip_thpt', render: d => d ? `<span class="actual-cell">${Number(d).toFixed(2)}</span>` : '-' },
                    { title: 'Pred DL Thpt (Mbps)', data: 'predicted_eric_dl_user_ip_thpt', render: d => d ? `<span class="pred-cell">${Number(d).toFixed(2)}</span>` : '-'}
                ], pageLength: 25
            });
        }

        function displayCongestionData() {
            $('#congestionTable').DataTable({
                serverSide: true, processing: true, scrollX: true, dom: 'frtip',
                ajax: { url: '/api/congestion_data', data: function(d) { return $.extend({}, d, Dashboard.state); }, dataSrc: 'data' },
                columns: [
                    { title: 'Sector ID', data: d => d.zoom_sector_id_override || d.zoom_sector_id || '-' },
                    { title: 'Week', data: 'week' },
                    { title: 'Operator', data: 'operator', render: d => d === 'Celcom' ? '<span class="px-2 py-1 rounded bg-blue-100 text-blue-800 text-xs font-bold">Celcom</span>' : (d === 'Digi' ? '<span class="px-2 py-1 rounded bg-yellow-100 text-yellow-800 text-xs font-bold">Digi</span>' : '-') },
                    { title: 'Category', data: 'area_target', render: d => { if (!d) return '-'; const str = String(d).toLowerCase(); if (str.includes('urban') || str.includes('kmc')) return `<span class="px-2 py-1 rounded bg-purple-100 text-purple-800 text-xs font-bold border border-purple-200">${d}</span>`; if (str.includes('outside')) return `<span class="px-2 py-1 rounded bg-teal-100 text-teal-800 text-xs font-bold border border-teal-200">${d}</span>`; return `<span class="text-xs font-semibold">${d}</span>`; } },
                    { title: 'Mode', data: 'bau_nic', render: d => { if (!d) return '-'; return String(d).toLowerCase().includes('nic') ? `<span class="px-2 py-1 rounded bg-orange-100 text-orange-800 text-xs font-bold border border-orange-200">NIC</span>` : `<span class="px-2 py-1 rounded bg-gray-100 text-gray-800 text-xs font-bold border border-gray-200">BAU</span>`; } },
                    { title: 'Max Users', data: d => d.eric_max_rrc_user || d.max_active_user || 0, render: d => `<span class="${d>=120?'text-red-600 font-bold':''}">${d}</span>` },
                    { title: 'PRB Util (%)', data: 'eric_prb_util_rate', render: (d, t, r) => { if (d == null) return '-'; let thresh = (r.area_target || '').toLowerCase().includes('urban') ? 80.0 : 92.0; return parseFloat(d) >= thresh ? `<span class="text-red-600 font-bold" title="Exceeds ${thresh}% limit">${Number(d).toFixed(2)}</span>` : Number(d).toFixed(2); } },
                    { title: 'DL Thpt (Mbps)', data: 'eric_dl_user_ip_thpt', render: (d, t, r) => { if (d == null) return '-'; let thresh = 3.0; const cat = (r.area_target|| '').toLowerCase(); const mode = (r.bau_nic || '').toLowerCase(); if (cat.includes('urban') || cat.includes('kmc')) thresh = mode.includes('nic') ? 7.0 : 5.0; return parseFloat(d) < thresh ? `<span class="text-red-600 font-bold" title="Below ${thresh} Mbps limit">${Number(d).toFixed(2)}</span>` : Number(d).toFixed(2); } },
                    { title: 'Congested Weeks', data: 'congested_weeks' }
                ], pageLength: 25
            });
        }

        function setupPlotAutocomplete(inputId, resultsId, onSelect) {
            const input = document.getElementById(inputId); const results = document.getElementById(resultsId); let debounceTimer;
            document.addEventListener('click', e => { if (!input.contains(e.target) && !results.contains(e.target)) results.classList.add('hidden'); });
            input.addEventListener('input', function() {
                clearTimeout(debounceTimer); const query = input.value.trim();
                if (query.length < 1) { results.classList.add('hidden'); return; }
                debounceTimer = setTimeout(async () => {
                    try {
                        const response = await fetch(`/api/site_ids?q=${encodeURIComponent(query)}`);
                        const data = await response.json();
                        results.innerHTML = '';
                        if (data.length > 0) {
                            data.forEach(siteId => {
                                const div = document.createElement('div'); div.className = 'autocomplete-item'; div.textContent = siteId;
                                div.addEventListener('click', () => { input.value = siteId; results.classList.add('hidden'); if(onSelect) onSelect(siteId); });
                                results.appendChild(div);
                            });
                            results.classList.remove('hidden');
                        } else {
                            const div = document.createElement('div'); div.className = 'autocomplete-item text-gray-400 italic cursor-default'; div.textContent = 'No matches found'; results.appendChild(div); results.classList.remove('hidden');
                        }
                    } catch (err) {}
                }, 300);
            });
        }
        setupPlotAutocomplete('plotSiteInput', 'plotSiteResults', null);

        document.getElementById('generatePlotBtn').addEventListener('click', async function(){
            const site = document.getElementById('plotSiteInput').value; const horizon = document.getElementById('plotHorizonSelect').value || 52;
            const container = document.getElementById('plotContainer'); const status = document.getElementById('plotStatus');
            if (!site) { status.textContent = 'Please type a valid Site ID.'; return; }
            container.innerHTML = `<div class="flex flex-col items-center justify-center h-96 text-gray-500"><i class="fas fa-circle-notch fa-spin text-4xl mb-3 text-blue-600"></i><p class="font-semibold">Generating interactive analysis...</p></div>`;
            status.textContent = 'Processing...';
            try {
                const resp = await fetch(`/plot?site_id=${encodeURIComponent(site)}&forecast_horizon=${horizon}`);
                const json = await resp.json();
                if (json.plot_image && typeof json.plot_image === 'string') {
                    status.textContent = ''; container.innerHTML = '';
                    const item = JSON.parse(json.plot_image); item.target_id = "plotContainer"; Bokeh.embed.embed_item(item);
                } else { container.innerHTML = `<div class="text-red-500 p-8">No data found for site <strong>${site}</strong></div>`; status.textContent = json.error || 'Error'; }
            } catch (err) { status.textContent = 'Plot failed'; container.innerHTML = '<div class="text-red-500 p-4">Connection error.</div>'; }
        });

        // --- NEW MODAL & UI SCRIPTS ---
        document.addEventListener("DOMContentLoaded", function () {
            const menuBtn = document.getElementById("menuBtn");
            const dropdown = document.getElementById("menuDropdown");
            if(menuBtn) {
                menuBtn.addEventListener("click", function(e){ e.stopPropagation(); dropdown.classList.toggle("open"); });
                document.addEventListener("click", function(e){ if (!menuBtn.contains(e.target) && !dropdown.contains(e.target)) dropdown.classList.remove("open"); });
            }
            pollUnreadCount(); setInterval(pollUnreadCount, 15000);
            // Auto-init the map since it's the landing page
            setTimeout(function(){
                if (typeof initMapIfNeeded === 'function') initMapIfNeeded();
                const p = document.getElementById('pegmanControl');
                if (p) p.style.visibility = 'visible';
            }, 300);
        });


        // --- SETTINGS ---
        window._openSettingsModal = async function() {
            document.getElementById('menuDropdown').classList.remove('open');
            try {
                const res = await fetch('/api/user/profile'); const data = await res.json();
                document.getElementById('settingsFullName').value = data.full_name || '';
                document.getElementById('settingsEmail').value = data.email || '';
                document.getElementById('settingsUsername').value = data.username || '';
            } catch(e) {}
            document.getElementById('settingsModal').classList.remove('hidden');
        }
        window._closeSettingsModal = function() { document.getElementById('settingsModal').classList.add('hidden'); document.getElementById('profileMsg').classList.add('hidden'); document.getElementById('passwordMsg').classList.add('hidden'); }
        async function saveProfileSettings() {
            const msgEl = document.getElementById('profileMsg'); msgEl.classList.add('hidden');
            try {
                const res = await fetch('/api/user/profile', { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ full_name: document.getElementById('settingsFullName').value, email: document.getElementById('settingsEmail').value }) });
                const data = await res.json();
                msgEl.textContent = data.message; msgEl.className = `text-sm text-center font-semibold rounded-lg py-2 ${data.success ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`; msgEl.classList.remove('hidden');
            } catch(e) { msgEl.textContent = 'Error'; msgEl.className = 'text-sm text-center font-semibold rounded-lg py-2 bg-red-100 text-red-700'; msgEl.classList.remove('hidden'); }
        }
        async function savePasswordSettings() {
            const msgEl = document.getElementById('passwordMsg'); const newPw = document.getElementById('settingsNewPassword').value; const confirmPw = document.getElementById('settingsConfirmPassword').value; msgEl.classList.add('hidden');
            if (newPw !== confirmPw) { msgEl.textContent = 'Passwords do not match'; msgEl.className = 'text-sm text-center font-semibold rounded-lg py-2 bg-red-100 text-red-700'; msgEl.classList.remove('hidden'); return; }
            try {
                const res = await fetch('/api/user/change-password', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({new_password: newPw}) });
                const data = await res.json();
                msgEl.textContent = data.message; msgEl.className = `text-sm text-center font-semibold rounded-lg py-2 ${data.success ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`; msgEl.classList.remove('hidden');
                if (data.success) { document.getElementById('settingsNewPassword').value = ''; document.getElementById('settingsConfirmPassword').value = ''; }
            } catch(e) { msgEl.textContent = 'Error'; msgEl.className = 'text-sm text-center font-semibold rounded-lg py-2 bg-red-100 text-red-700'; msgEl.classList.remove('hidden'); }
        }

        // --- REVIEWS ---
        let _reviewRating = 0;
        window._openReviewModal = function() { document.getElementById('reviewModal').classList.remove('hidden'); switchReviewTab('write'); }
        window._closeReviewModal = function() { document.getElementById('reviewModal').classList.add('hidden'); resetReviewForm(); }
        function switchReviewTab(tab) {
            const write = document.getElementById('reviewPanelWrite'); const read = document.getElementById('reviewPanelRead');
            const tabW = document.getElementById('reviewTabWrite'); const tabR = document.getElementById('reviewTabRead');
            if (tab === 'write') {
                write.classList.remove('hidden'); read.classList.add('hidden');
                tabW.className = 'flex-1 py-3 text-sm font-semibold text-yellow-600 border-b-2 border-yellow-500 bg-yellow-50';
                tabR.className = 'flex-1 py-3 text-sm font-semibold text-gray-500 hover:text-gray-700 hover:bg-gray-50';
            } else {
                write.classList.add('hidden'); read.classList.remove('hidden');
                tabR.className = 'flex-1 py-3 text-sm font-semibold text-yellow-600 border-b-2 border-yellow-500 bg-yellow-50';
                tabW.className = 'flex-1 py-3 text-sm font-semibold text-gray-500 hover:text-gray-700 hover:bg-gray-50';
                loadReviews();
            }
        }
        function resetReviewForm() {
            _reviewRating = 0; document.getElementById('reviewRating').value = 0; document.getElementById('reviewTitle').value = ''; document.getElementById('reviewBody').value = ''; document.getElementById('reviewAnon').checked = false; document.getElementById('reviewCategory').value = 'General'; document.querySelectorAll('.review-star').forEach(s => s.style.color = '#d1d5db'); document.getElementById('reviewMsg').classList.add('hidden');
        }
        document.querySelectorAll('.review-star').forEach(star => {
            star.addEventListener('click', function() { _reviewRating = parseInt(this.dataset.val); document.getElementById('reviewRating').value = _reviewRating; document.querySelectorAll('.review-star').forEach(s => s.style.color = parseInt(s.dataset.val) <= _reviewRating ? '#f59e0b' : '#d1d5db'); });
            star.addEventListener('mouseover', function() { const val = parseInt(this.dataset.val); document.querySelectorAll('.review-star').forEach(s => s.style.color = parseInt(s.dataset.val) <= val ? '#f59e0b' : '#d1d5db'); });
            star.addEventListener('mouseout', function() { document.querySelectorAll('.review-star').forEach(s => s.style.color = parseInt(s.dataset.val) <= _reviewRating ? '#f59e0b' : '#d1d5db'); });
        });
        async function submitReview() {
            const rating = parseInt(document.getElementById('reviewRating').value); const body = document.getElementById('reviewBody').value.trim();
            if (!rating) { showReviewMsg('Please select a star rating.', 'error'); return; }
            if (!body) { showReviewMsg('Please enter your feedback.', 'error'); return; }
            const btn = document.getElementById('reviewSubmitBtn'); btn.disabled = true; btn.innerHTML = '<i class="fas fa-circle-notch fa-spin mr-1"></i> Submitting...';
            try {
                const res = await fetch('/api/reviews', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ category: document.getElementById('reviewCategory').value, rating, title: document.getElementById('reviewTitle').value.trim(), body, is_anonymous: document.getElementById('reviewAnon').checked }) });
                const data = await res.json();
                if (res.ok && data.success) { showReviewMsg('Thank you for your review! 🎉', 'success'); setTimeout(() => { resetReviewForm(); switchReviewTab('read'); }, 1200); }
                else { showReviewMsg(data.error || 'Failed to submit.', 'error'); }
            } catch(e) { showReviewMsg('Network error.', 'error'); } finally { btn.disabled = false; btn.innerHTML = '<i class="fas fa-paper-plane mr-1"></i> Submit'; }
        }
        function showReviewMsg(text, type) { const msg = document.getElementById('reviewMsg'); msg.textContent = text; msg.className = `text-sm text-center font-semibold rounded-lg py-2 ${type==='success' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`; msg.classList.remove('hidden'); }
        async function loadReviews() {
            const list = document.getElementById('reviewsList'); const cat = document.getElementById('reviewFilterCat').value;
            list.innerHTML = '<div class="text-center text-gray-400 text-sm py-8"><i class="fas fa-circle-notch fa-spin mr-2"></i>Loading...</div>';
            try {
                const res = await fetch(`/api/reviews?limit=50${cat ? '&category='+encodeURIComponent(cat) : ''}`); const rows = await res.json();
                if (!rows.length) { list.innerHTML = '<div class="text-center text-gray-400 text-sm py-8"><i class="fas fa-inbox mr-2"></i>No reviews yet.</div>'; return; }
                list.innerHTML = rows.map(r => `
                    <div class="bg-gray-50 border border-gray-200 rounded-xl p-3">
                        <div class="flex justify-between items-start">
                            <div><span class="text-xs font-bold text-gray-700">${escapeHtml(r.username)}</span><span class="ml-2 text-[10px] bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full font-semibold">${escapeHtml(r.category)}</span></div>
                            <span class="text-yellow-400 text-sm">${'★'.repeat(r.rating)}${'☆'.repeat(5-r.rating)}</span>
                        </div>
                        ${r.title ? `<p class="text-sm font-semibold text-gray-800 mt-1">${escapeHtml(r.title)}</p>` : ''}
                        <p class="text-xs text-gray-600 mt-1 leading-relaxed">${escapeHtml(r.body)}</p>
                        <p class="text-[10px] text-gray-400 mt-1">${new Date(r.created_at).toLocaleDateString('en-MY',{year:'numeric',month:'short',day:'numeric'})}</p>
                    </div>`).join('');
            } catch(e) { list.innerHTML = '<div class="text-center text-red-400 text-sm py-8">Failed to load reviews.</div>'; }
        }

        // --- MESSAGING ---
        let currentConversationId = null; let allMessages = []; let allConversations = []; let chatPollInterval = null;
        const EMOJIS = ['😀','😂','😍','🥰','😎','🤔','😅','😭','😤','🙏','👍','👎','❤️','🔥','✅','⚡','🎉','💯','🚀','👏','😊','🤣','😆','😁','😋','🤩','😇','😴','🤗','😏','😒','🙄','😑','🤐','😬','🥲','😓','😥','😢','😨','😰','😱','🤯','🤫','🫡','🫠','💀','👻'];
        window._openMessagingModal = async function() {
            try { document.getElementById('menuDropdown').classList.remove('open'); } catch(e) {}
            document.getElementById('messagingModal').classList.remove('hidden'); buildEmojiPicker(); await loadConversations(); await loadUsersForMessaging();
        }
        window._closeMessagingModal = function() { document.getElementById('messagingModal').classList.add('hidden'); if (chatPollInterval) { clearInterval(chatPollInterval); chatPollInterval = null; } document.getElementById('emojiPicker').classList.add('hidden'); }
        function buildEmojiPicker() {
            const picker = document.querySelector('#emojiPicker > div'); if (picker.childElementCount > 0) return;
            EMOJIS.forEach(e => { const btn = document.createElement('button'); btn.textContent = e; btn.className = 'hover:bg-gray-100 rounded p-0.5 cursor-pointer'; btn.onclick = () => insertEmoji(e); picker.appendChild(btn); });
        }
        function toggleEmojiPicker() { document.getElementById('emojiPicker').classList.toggle('hidden'); }
        function insertEmoji(emoji) { const input = document.getElementById('internalChatInput'); const pos = input.selectionStart; input.value = input.value.slice(0, pos)+ emoji + input.value.slice(pos); input.focus(); input.selectionStart = input.selectionEnd = pos + emoji.length; }
        function filterConversations() {
            const q = document.getElementById('msgSearchInput').value.toLowerCase().trim(); const list = document.getElementById('conversationList'); list.innerHTML = '';
            const filtered = q ? allConversations.filter(c => { const name = (c.title || c.partner_name || '').toLowerCase(); const last = (c.last_message || '').toLowerCase(); return name.includes(q) || last.includes(q); }) : allConversations;
            if (filtered.length === 0) { list.innerHTML = '<p class="text-xs text-gray-400 text-center p-4">No results</p>'; return; }
            renderConversationItems(filtered);
        }
        function renderConversationItems(convs) {
            const list = document.getElementById('conversationList'); list.innerHTML = '';
            convs.forEach(c => {
                const displayName = c.title || c.partner_name; const isGroup = !!c.is_group; const div = document.createElement('div');
                div.className = `p-3 cursor-pointer border-b border-gray-100 hover:bg-gray-50 transition ${c.id === currentConversationId ? 'bg-blue-50' : ''}`;
                div.onclick = () => openConversation(c.id, displayName, isGroup);
                div.innerHTML = `<div class="flex items-center justify-between"><p class="font-semibold text-sm text-gray-800 truncate flex items-center gap-1">${isGroup ?'<i class="fas fa-users text-purple-500 text-xs flex-shrink-0"></i>' : ''}${escapeHtml(displayName)}</p>${c.unread_count > 0 ? `<span class="bg-red-500 text-white text-xs rounded-full px-1.5 py-0.5 ml-1 flex-shrink-0">${c.unread_count}</span>` : ''}</div><p class="text-xs text-gray-500 truncate mt-0.5">${c.last_message ? escapeHtml(c.last_message) : ''}</p><p class="text-xs text-gray-400 mt-0.5">${c.last_time ? new Date(c.last_time).toLocaleString() : ''}</p>`;
                list.appendChild(div);
            });
        }
        async function loadConversations() {
            try {
                const res = await fetch('/api/messages/conversations'); allConversations = await res.json(); renderConversationItems(allConversations);
                const total = allConversations.reduce((s, c) => s + (c.unread_count || 0), 0);
                ['navUnreadBadge'].forEach(id => { const el = document.getElementById(id); if (!el) return; if (total > 0) { el.textContent = total; el.classList.remove('hidden'); } else el.classList.add('hidden'); });
            } catch(e) {}
        }
        async function loadUsersForMessaging() {
            try {
                const res = await fetch('/api/messages/users'); const users = await res.json(); const sel = document.getElementById('newMsgRecipient');
                sel.innerHTML = '<option value="">Select recipient...</option>'; users.forEach(u => { sel.innerHTML += `<option value="${u.id}">${escapeHtml(u.full_name)} (@${escapeHtml(u.username)})</option>`; });
            } catch(e) {}
        }
        async function openConversation(convId, partnerName, isGroup) {
            currentConversationId = convId; document.getElementById('chatEmpty').classList.add('hidden'); document.getElementById('newConvForm').classList.add('hidden'); document.getElementById('newGroupForm').classList.add('hidden'); document.getElementById('groupMembersPanel').classList.add('hidden'); document.getElementById('msgInChatSearchBar').classList.add('hidden'); document.getElementById('renameGroupPanel').classList.add('hidden'); document.getElementById('emojiPicker').classList.add('hidden'); document.getElementById('activeChat').classList.remove('hidden'); document.getElementById('chatPartnerName').textContent = partnerName;
            const groupActions = document.getElementById('groupChatActions'); const leaveGroupBtn = document.getElementById('leaveGroupBtn'); const deleteGroupBtn = document.getElementById('deleteGroupBtn'); const renameGroupBtn = document.getElementById('renameGroupBtn');
            let isGroupAdmin = false;
            if (isGroup) {
                groupActions.classList.remove('hidden');
                try {
                    const mRes = await fetch(`/api/messages/group/${convId}/members`); const members = await mRes.json();
                    const me = members.find(m => m.id === window.USER_DATA.userId); isGroupAdmin = !!(me && me.is_admin);
                } catch(e) { }
                leaveGroupBtn.classList.remove('hidden');
                if (isGroupAdmin) { deleteGroupBtn.classList.remove('hidden'); renameGroupBtn.classList.remove('hidden'); }
                else { deleteGroupBtn.classList.add('hidden'); renameGroupBtn.classList.add('hidden'); }
            } else { groupActions.classList.add('hidden'); renameGroupBtn.classList.add('hidden'); }
            await loadMessages(convId); await loadConversations();
            if (chatPollInterval) clearInterval(chatPollInterval); chatPollInterval = setInterval(() => loadMessages(convId), 5000);
        }
        async function showGroupMembers() {
            const panel = document.getElementById('groupMembersPanel'); if (!panel.classList.contains('hidden')) { panel.classList.add('hidden'); return; }
            try {
                const res = await fetch(`/api/messages/group/${currentConversationId}/members`); const members = await res.json();
                const container = document.getElementById('groupMembersList'); container.innerHTML = '';
                members.forEach(m => { const chip = document.createElement('span'); chip.className = 'text-xs bg-white border border-purple-200 text-purple-700 rounded-full px-2 py-0.5 flex items-center gap-1'; chip.innerHTML = `<i class="fas fa-user text-purple-400" style="font-size:0.6rem"></i>${escapeHtml(m.full_name || m.username)}`; container.appendChild(chip); });
                panel.classList.remove('hidden');
            } catch(e) {}
        }
        async function leaveGroup() {
            if (!confirm('Leave this group?')) return;
            try {
                const res = await fetch(`/api/messages/group/${currentConversationId}/leave`, { method: 'POST', headers: {'Content-Type': 'application/json'} }); const data = await res.json();
                if (data.success) { currentConversationId = null; document.getElementById('activeChat').classList.add('hidden'); document.getElementById('chatEmpty').classList.remove('hidden'); await loadConversations(); } else { alert(data.message); }
            } catch(e) { alert('Error leaving group'); }
        }
        async function deleteGroup() {
            if (!confirm('Permanently delete this group?')) return;
            try {
                const res = await fetch(`/api/messages/group/${currentConversationId}/delete`, { method: 'POST', headers: {'Content-Type': 'application/json'} }); const data = await res.json();
                if (data.success) { currentConversationId = null; document.getElementById('activeChat').classList.add('hidden'); document.getElementById('chatEmpty').classList.remove('hidden'); await loadConversations(); } else { alert(data.message); }
            } catch(e) { alert('Error deleting group'); }
        }
        function showRenameGroup() {
            const panel = document.getElementById('renameGroupPanel'); if (!panel.classList.contains('hidden')) { hideRenameGroup(); return; }
            document.getElementById('renameGroupInput').value = document.getElementById('chatPartnerName').textContent; document.getElementById('renameGroupStatus').classList.add('hidden'); document.getElementById('groupMembersPanel').classList.add('hidden'); panel.classList.remove('hidden'); document.getElementById('renameGroupInput').focus();
        }
        function hideRenameGroup() { document.getElementById('renameGroupPanel').classList.add('hidden'); document.getElementById('renameGroupStatus').classList.add('hidden'); }
        async function submitRenameGroup() {
            const input = document.getElementById('renameGroupInput'); const status = document.getElementById('renameGroupStatus'); const newName = input.value.trim();
            if (!newName) { status.textContent = 'Name cannot be empty.'; status.className = 'text-xs mt-1 text-red-500'; status.classList.remove('hidden'); return; }
            try {
                const res = await fetch(`/api/messages/group/${currentConversationId}/rename`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: newName}) }); const data = await res.json();
                if (data.success) { document.getElementById('chatPartnerName').textContent = newName; status.textContent = 'Group renamed!'; status.className = 'text-xs mt-1 text-green-600'; status.classList.remove('hidden'); await loadConversations(); setTimeout(hideRenameGroup, 1200); }
                else { status.textContent = data.message; status.className = 'text-xs mt-1 text-red-500'; status.classList.remove('hidden'); }
            } catch(e) { status.textContent = 'Error renaming.'; status.className = 'text-xs mt-1 text-red-500'; status.classList.remove('hidden'); }
        }
        function toggleMsgInChatSearch() { const bar = document.getElementById('msgInChatSearchBar'); bar.classList.toggle('hidden'); if (!bar.classList.contains('hidden')) { document.getElementById('msgInChatInput').focus(); } else { document.getElementById('msgInChatInput').value = ''; renderMessages(allMessages); } }
        function closeMsgInChatSearch() { document.getElementById('msgInChatSearchBar').classList.add('hidden'); document.getElementById('msgInChatInput').value = ''; renderMessages(allMessages); }
        function filterMessagesInChat() { const q = document.getElementById('msgInChatInput').value.toLowerCase().trim(); if (!q) { renderMessages(allMessages); return; } const filtered = allMessages.filter(m => (m.content || '').toLowerCase().includes(q)); renderMessages(filtered, q); }
        function renderMessages(msgs, highlight) {
            const container = document.getElementById('chatMessagesPanel'); container.innerHTML = '';
            msgs.forEach(m => {
                const isMe = m.is_mine; let content = escapeHtml(m.content);
                if (highlight) { const re = new RegExp('(' + highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi'); content = content.replace(re, '<mark class="bg-yellow-200 rounded px-0.5">$1</mark>'); }
                const div = document.createElement('div'); div.className = `flex ${isMe ? 'justify-end' : 'justify-start'}`;
                div.innerHTML = `<div class="max-w-xs px-3 py-2 rounded-2xl text-sm shadow-sm ${isMe ? 'bg-blue-600 text-white rounded-br-sm' : 'bg-gray-100 text-gray-800 rounded-bl-sm'}">${(!isMe && m.sender_name) ? `<p class="text-xs font-semibold mb-1 text-purple-600">${escapeHtml(m.sender_name)}</p>` : ''}<p>${content}</p><p class="text-xs mt-1 opacity-60">${new Date(m.sent_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</p></div>`;
                container.appendChild(div);
            });
            container.scrollTop = container.scrollHeight;
        }
        async function loadMessages(convId) {
            try { const res = await fetch(`/api/messages/conversation/${convId}`); allMessages = await res.json(); const q = document.getElementById('msgInChatInput') ? document.getElementById('msgInChatInput').value.toLowerCase().trim() : ''; if (q) filterMessagesInChat(); else renderMessages(allMessages); } catch(e) {}
        }
        async function sendChatMessage() {
            const input = document.getElementById('internalChatInput'); const content = input.value.trim(); if (!content || !currentConversationId) return; input.value = ''; document.getElementById('emojiPicker').classList.add('hidden');
            try { await fetch('/api/messages/send', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({conversation_id: currentConversationId, content}) }); await loadMessages(currentConversationId); await loadConversations(); } catch(e) {}
        }
        function showNewConversation() { document.getElementById('chatEmpty').classList.add('hidden'); document.getElementById('activeChat').classList.add('hidden'); document.getElementById('newGroupForm').classList.add('hidden'); document.getElementById('newConvForm').classList.remove('hidden'); document.getElementById('newMsgStatus').classList.add('hidden'); if (chatPollInterval) { clearInterval(chatPollInterval); chatPollInterval = null; } }
        function showNewGroup() { document.getElementById('chatEmpty').classList.add('hidden'); document.getElementById('activeChat').classList.add('hidden'); document.getElementById('newConvForm').classList.add('hidden'); document.getElementById('newGroupForm').classList.remove('hidden'); document.getElementById('newGroupStatus').classList.add('hidden'); if (chatPollInterval) { clearInterval(chatPollInterval); chatPollInterval = null; } loadUsersForGroup(); }
        async function loadUsersForGroup() {
            try { const res = await fetch('/api/messages/users'); const users = await res.json(); const box = document.getElementById('groupMemberCheckboxes'); box.innerHTML = ''; users.forEach(u => { box.innerHTML += `<label class="flex items-center gap-2 p-1 hover:bg-gray-50 rounded cursor-pointer"><input type="checkbox" class="group-member-cb accent-purple-600" value="${u.id}"><span>${escapeHtml(u.full_name)} <span class="text-gray-400">@${escapeHtml(u.username)}</span></span></label>`; }); } catch(e) {}
        }
        async function sendNewGroup() {
            const name = document.getElementById('newGroupName').value.trim(); const content = document.getElementById('newGroupMessage').value.trim(); const statusEl = document.getElementById('newGroupStatus'); const memberIds = [...document.querySelectorAll('.group-member-cb:checked')].map(cb => parseInt(cb.value));
            if (!name) { statusEl.textContent = 'Enter a group name.'; statusEl.className = 'mt-2 text-xs text-center font-semibold text-red-600'; statusEl.classList.remove('hidden'); return; }
            if (memberIds.length === 0) { statusEl.textContent = 'Select at least one member.'; statusEl.className = 'mt-2 text-xs text-center font-semibold text-red-600';statusEl.classList.remove('hidden'); return; }
            if (!content) { statusEl.textContent = 'Write a first message.'; statusEl.className = 'mt-2 text-xs text-center font-semibold text-red-600'; statusEl.classList.remove('hidden'); return; }
            try {
                const res = await fetch('/api/messages/group/new', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: name, member_ids: memberIds, content}) }); const data = await res.json();
                if (data.success) { document.getElementById('newGroupName').value = ''; document.getElementById('newGroupMessage').value = ''; statusEl.classList.add('hidden'); await loadConversations(); openConversation(data.conversation_id, data.title, true); }
                else { statusEl.textContent = data.message || 'Failed'; statusEl.className = 'mt-2 text-xs text-center font-semibold text-red-600'; statusEl.classList.remove('hidden'); }
            } catch(e) { statusEl.textContent = 'Error creating group.'; statusEl.className = 'mt-2 text-xs text-center font-semibold text-red-600'; statusEl.classList.remove('hidden'); }
        }
        function cancelNewConv() { document.getElementById('newConvForm').classList.add('hidden'); document.getElementById('newGroupForm').classList.add('hidden'); document.getElementById('chatEmpty').classList.remove('hidden'); }
        async function sendNewMessage() {
            const recipientId = document.getElementById('newMsgRecipient').value; const content = document.getElementById('newMsgContent').value.trim(); const statusEl = document.getElementById('newMsgStatus');
            if (!recipientId || !content) { statusEl.textContent = 'Select recipient and write a message.'; statusEl.className = 'mt-2 text-xs text-center font-semibold text-red-600'; statusEl.classList.remove('hidden'); return; }
            try {
                const res = await fetch('/api/messages/new', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({recipient_id: parseInt(recipientId), content}) }); const data = await res.json();
                if (data.success) { document.getElementById('newMsgContent').value = ''; document.getElementById('newMsgRecipient').value = ''; statusEl.classList.add('hidden'); await loadConversations(); openConversation(data.conversation_id, data.partner_name, false); }
                else { statusEl.textContent = data.message || 'Failed'; statusEl.className = 'mt-2 text-xs text-center font-semibold text-red-600'; statusEl.classList.remove('hidden'); }
            } catch(e) { statusEl.textContent = 'Error sending message.'; statusEl.className = 'mt-2 text-xs text-center font-semibold text-red-600'; statusEl.classList.remove('hidden'); }
        }
        async function pollUnreadCount() {
            try {
                const res = await fetch('/api/messages/unread-count'); const data = await res.json();
                const navBadge = document.getElementById('navUnreadBadge');
                if (data.count > 0) { navBadge.textContent = data.count; navBadge.classList.remove('hidden'); } else { navBadge.classList.add('hidden'); }
            } catch(e) {}
        }
        function escapeHtml(text) { const div = document.createElement('div'); div.appendChild(document.createTextNode(text)); return div.innerHTML; }

        // ================================================================
        // SPLIT SCREEN — Actual (left) vs Forecast (right)
        // Both maps show ALL sites. Left = actual congestion colours.
        // Right = forecast congestion colours for chosen quarterly snapshot.
        // Pan/zoom is synced between both maps.
        // ================================================================
        let splitMap              = null;
        let splitActive           = false;
        let splitQuarter          = 39;
        let splitSyncLock         = false;
        let splitCluster          = null;
        let splitCongestedCluster = null;

        function toggleSplitScreen() {
            const btn = document.getElementById('splitScreenBtn');
            if (splitActive) {
                splitActive = false;
                btn.classList.remove('active');
                document.getElementById('map').classList.remove('split');
                document.getElementById('splitWrapper').classList.remove('active');
                map.off('moveend zoomend', _syncLeftToRight);
                if (splitMap)      { splitMap.remove();      splitMap      = null; }
                if (splitActualMap) { splitActualMap.remove(); splitActualMap = null; }
                splitSatLayer = splitLabelsLayer = splitStreetLayer = splitDemLayer = null;
                splitActualSatLayer = splitActualLabelsLayer = splitActualStreetLayer = splitActualDemLayer = null;
                setTimeout(() => { if (map) map.invalidateSize(); }, 50);
                // Restore compass to original position
                const c = document.getElementById('northCompass');
                if (c) c.style.top = '80px';
            } else {
                splitActive = true;
                btn.classList.add('active');
                document.getElementById('map').classList.add('split');
                document.getElementById('splitWrapper').classList.add('active');
                // Lower compass so it clears zoom buttons on right pane
                const c = document.getElementById('northCompass');
                if (c) c.style.top = '130px';
                setTimeout(() => {
                    _initSplitMaps();
                    if (map) map.invalidateSize();
                }, 100);
            }
        }

        let splitActualMap = null; // left pane Leaflet instance

        function _syncLeftToRight() {
            if (splitSyncLock || !splitActive || !splitMap) return;
            splitSyncLock = true;
            splitMap.setView(map.getCenter(), map.getZoom(), { animate: false });
            if (splitActualMap) splitActualMap.setView(map.getCenter(), map.getZoom(), { animate: false });
            splitSyncLock = false;
        }

        function _syncRightToLeft() {
            if (splitSyncLock || !splitActive) return;
            splitSyncLock = true;
            map.setView(splitMap.getCenter(), splitMap.getZoom(), { animate: false });
            if (splitActualMap) splitActualMap.setView(splitMap.getCenter(), splitMap.getZoom(), { animate: false });
            splitSyncLock = false;
        }

        function _initSplitMaps() {
            const center = map.getCenter();
            const zoom   = map.getZoom();

            // ── LEFT PANE — mirror of actual-state map ──
            if (!splitActualMap) {
                splitActualMap = L.map('splitMapLeft', { zoomControl: false, preferCanvas: false, maxZoom: 19 })
                                  .setView(center, zoom);
                setTimeout(() => splitActualMap.invalidateSize(), 50);
                L.control.zoom({ position: 'topright' }).addTo(splitActualMap);

                // Same basemap as current left map
                if (isSatelliteMode) {
                    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom:19 }).addTo(splitActualMap);
                    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png', { subdomains:'abcd', maxZoom:19 }).addTo(splitActualMap);
                } else {
                    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { subdomains:'abcd', maxZoom:19 }).addTo(splitActualMap);
                }

                // Plot same sites with actual congestion colours
                if (siteDataCache && siteDataCache.length) {
                    const actualNormal = L.markerClusterGroup({ chunkedLoading:true, maxClusterRadius:60,
                        iconCreateFunction: c => new L.DivIcon({ html:'<div class="cluster-base cluster-normal"><span>'+c.getChildCount()+'</span></div>', className:'marker-cluster', iconSize:new L.Point(40,40) }) });
                    const actualCong   = L.markerClusterGroup({ chunkedLoading:true, maxClusterRadius:60,
                        iconCreateFunction: c => new L.DivIcon({ html:'<div class="cluster-base cluster-congested"><span>'+c.getChildCount()+'</span></div>', className:'marker-cluster', iconSize:new L.Point(40,40) }) });

                    siteDataCache.forEach(site => {
                        const isCong = site.congested;
                        const color  = isCong ? '#dc2626' : '#2563eb';
                        const icon   = L.divIcon({ className:'custom-pin',
                            html:`<div style="position:absolute;bottom:16px;left:50%;transform:translateX(-50%);font-size:10px;font-weight:900;color:#1e293b;text-shadow:2px 2px 0 #fff,-2px -2px 0 #fff,2px -2px 0 #fff,-2px 2px 0 #fff;white-space:nowrap;pointer-events:none;">${site.site_id}</div>
                                  <div style="background-color:${color};width:14px;height:14px;border-radius:50%;border:2px solid white;box-shadow:0 2px 5px rgba(0,0,0,0.3);"></div>`,
                            iconSize:[14,14], iconAnchor:[7,7] });
                        const m = L.marker([site.lat, site.lng], { icon })
                            .bindPopup(`<div style="font-family:'Inter',sans-serif;min-width:180px;">
                                <div style="background:#0f2d52;color:white;padding:8px 12px;border-radius:6px 6px 0 0;font-weight:800;">${site.site_id} — Actual</div>
                                <div style="padding:8px 12px;font-size:.78rem;">
                                    <div><span style="color:#64748b;">Status:</span> <b style="color:${isCong?'#dc2626':'#16a34a'}">${isCong?'⚠ Congested':'✓ Normal'}</b></div>
                                </div></div>`);
                        if (isCong) actualCong.addLayer(m); else actualNormal.addLayer(m);
                    });
                    splitActualMap.addLayer(actualNormal);
                    splitActualMap.addLayer(actualCong);
                }

                // Label overlay
                const lblLeft = L.control({ position: 'topleft' });
                lblLeft.onAdd = () => { const d = L.DomUtil.create('div'); d.innerHTML = '<div style="background:#0f2d52;color:white;padding:4px 10px;border-radius:0 0 6px 0;font-size:.72rem;font-weight:800;box-shadow:0 2px 6px rgba(0,0,0,.2);">📍 Current Status</div>'; return d; };
                lblLeft.addTo(splitActualMap);

                splitActualMap.on('moveend zoomend', _syncRightToLeft);
            }

            // ── RIGHT PANE — forecast map ──
            if (!splitMap) {
                splitMap = L.map('splitForecastMap', { zoomControl: false, preferCanvas: false, maxZoom: 19 })
                            .setView(center, zoom);
                setTimeout(() => { splitMap.invalidateSize(); _ensureSplitLayers(); _loadForecastMarkers(); }, 50);
                L.control.zoom({ position: 'topright' }).addTo(splitMap);

                // Label overlay
                const lblRight = L.control({ position: 'topleft' });
                lblRight.onAdd = () => { const d = L.DomUtil.create('div'); d.innerHTML = '<div style="background:#1e3a8a;color:white;padding:4px 10px;border-radius:0 0 6px 0;font-size:.72rem;font-weight:800;box-shadow:0 2px 6px rgba(0,0,0,.2);">🔮 Forecast</div>'; return d; };
                lblRight.addTo(splitMap);

                splitCluster = L.markerClusterGroup({ chunkedLoading:true, maxClusterRadius:60,
                    iconCreateFunction: c => new L.DivIcon({ html:'<div class="cluster-base cluster-normal"><span>'+c.getChildCount()+'</span></div>', className:'marker-cluster', iconSize:new L.Point(40,40) }) });
                splitCongestedCluster = L.markerClusterGroup({ chunkedLoading:true, maxClusterRadius:60,
                    iconCreateFunction: c => new L.DivIcon({ html:'<div class="cluster-base cluster-congested"><span>'+c.getChildCount()+'</span></div>', className:'marker-cluster', iconSize:new L.Point(40,40) }) });
                splitMap.addLayer(splitCluster);
                splitMap.addLayer(splitCongestedCluster);

                map.on('moveend zoomend', _syncLeftToRight);
                splitMap.on('moveend zoomend', _syncRightToLeft);
            }
        }

        async function _loadForecastMarkers() {
            if (!siteDataCache || !siteDataCache.length) {
                splitSetStatus('Waiting for site data…'); return;
            }
            splitSetStatus('<i class="fas fa-circle-notch fa-spin"></i> Loading forecast…');

            const year = document.getElementById('splitYearSelect').value;
            const qLabels = { 13:'Q1 Wk 13', 26:'Q2 Wk 26', 39:'Q3 Wk 39', 52:'Q4 Wk 52' };

            try {
                // Fetch forecast congestion flags for all sectors at chosen quarter
                const res  = await fetch(`/api/forecast_data?year=${year}&week=${splitQuarter}&length=99999&start=0`);
                const json = await res.json();
                const rows = (json.data || []);

                // Build site-level congestion lookup from sector rows
                const congestedSites = new Set();
                rows.forEach(r => {
                    if (r.congested) {
                        const sid = String(r.zoom_sector_id || '').split('_')[0].toUpperCase();
                        congestedSites.add(sid);
                    }
                });

                // Rebuild right-map markers
                splitCluster.clearLayers();
                splitCongestedCluster.clearLayers();

                siteDataCache.forEach(site => {
                    const sid        = site.site_id.toUpperCase();
                    const isCongested = congestedSites.has(sid);
                    const color      = isCongested ? '#dc2626' : '#2563eb';

                    const icon = L.divIcon({
                        className: 'custom-pin',
                        html: `<div style="position:absolute;bottom:16px;left:50%;transform:translateX(-50%);font-size:10px;font-weight:900;color:#1e293b;text-shadow:2px 2px 0 #fff,-2px -2px 0 #fff,2px -2px 0 #fff,-2px 2px 0 #fff;white-space:nowrap;pointer-events:none;">${site.site_id}</div>
                               <div style="background-color:${color};width:14px;height:14px;border-radius:50%;border:2px solid white;box-shadow:0 2px 5px rgba(0,0,0,0.3);"></div>`,
                        iconSize: [14,14], iconAnchor: [7,7]
                    });

                    const marker = L.marker([site.lat, site.lng], { icon })
                        .bindPopup(`<div style="font-family:'Inter',sans-serif;min-width:180px;">
                            <div style="background:#0f2d52;color:white;padding:8px 12px;border-radius:6px 6px 0 0;margin:-1px -1px 0;font-weight:800;">${site.site_id}</div>
                            <div style="padding:8px 12px;font-size:.78rem;">
                                <div style="margin-bottom:4px;"><span style="color:#64748b;">Region:</span> <b>${site.region||'—'}</b></div>
                                <div style="margin-bottom:4px;"><span style="color:#64748b;">Quarter:</span> <b>${qLabels[splitQuarter]} · ${year}</b></div>
                                <div><span style="color:#64748b;">Forecast Status:</span> <b style="color:${isCongested?'#dc2626':'#16a34a'}">${isCongested?'⚠ Congested':'✓ Normal'}</b></div>
                            </div>
                        </div>`);

                    if (isCongested) {
                        splitCongestedCluster.addLayer(marker);
                    } else {
                        splitCluster.addLayer(marker);
                    }
                });

                const found   = rows.length > 0;
                const noData  = !found ? ' · <span style="color:#f59e0b">No forecast data for this quarter</span>' : '';
                splitSetStatus(qLabels[splitQuarter] + ' · ' + year + noData);

            } catch(e) {
                console.error('Split screen forecast error:', e);
                splitSetStatus('<span style="color:#dc2626">Failed to load forecast</span>');
            }
        }

        function splitSetStatus(html) {
            const el = document.getElementById('splitStatusText');
            if (el) el.innerHTML = html;
        }

        function setSplitQuarter(week) {
            splitQuarter = week;
            [13,26,39,52].forEach((w,i) => {
                const b = document.getElementById('sq'+(i+1));
                if (b) b.classList.toggle('active', w === week);
            });
            if (splitActive && splitMap) _loadForecastMarkers();
        }

        function refreshSplitMarkers() {
            if (splitActive && splitMap) _loadForecastMarkers();
        }

