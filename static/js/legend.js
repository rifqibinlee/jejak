        window._driveTestLegendClusters = [];

        function _legendRow(iconHtml, label) {
            return '<div class="flex items-center gap-3 mb-1">' + iconHtml +
                '<span class="text-xs font-bold text-gray-700">' + label + '</span></div>';
        }
        function _legendSection(title, rowsHtml) {
            if (!rowsHtml) return '';
            return '<div class="pt-2 mt-1 border-t border-gray-100 first:border-t-0 first:mt-0 first:pt-0">' +
                '<p class="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1.5">' + title + '</p>' +
                rowsHtml + '</div>';
        }

        function updateMapLegend() {
            var el = document.getElementById('legendDynamicContent');
            var emptyHint = document.getElementById('legendEmptyHint');
            if (!el) return;
            var html = '';
            var ls = typeof layerStates !== 'undefined' ? layerStates : {};

            if (ls.clusters) {
                html += _legendSection('Sites', _legendRow(
                    '<div class="w-3 h-3 rounded-full bg-white border-2 border-blue-600 shadow-sm flex-shrink-0"></div>',
                    'All Sites (Clusters)'));
            }
            if (ls.congestion) {
                html += _legendRow(
                    '<div class="w-3 h-3 rounded-full bg-white border-2 border-red-600 shadow-sm flex-shrink-0"></div>',
                    'Congested Site');
            }
            if (ls['5g']) html += _legendRow('<div class="w-3 h-3 rounded-full bg-yellow-400 border border-yellow-600 flex-shrink-0"></div>', '5G (800m)');
            if (ls['4g']) html += _legendRow('<div class="w-3 h-3 rounded-full bg-blue-500 border border-blue-700 flex-shrink-0"></div>', '4G (3km)');
            if (ls['3g']) html += _legendRow('<div class="w-3 h-3 rounded-full bg-orange-400 border border-orange-600 flex-shrink-0"></div>', '3G Coverage');
            if (ls['2g']) html += _legendRow('<div class="w-3 h-3 rounded-full bg-gray-400 border border-gray-600 flex-shrink-0"></div>', '2G (30km)');

            var driveOn = ls.sig100_120 || ls.sig121_130 || ls.sig131_worse || ls.noise;
            if (driveOn) {
                var driveRows = '';
                driveRows += _legendRow(
                    '<div class="w-2.5 h-2.5 bg-slate-800 border border-white flex-shrink-0"></div>',
                    'MR drive test (square)');
                driveRows += _legendRow(
                    '<div class="w-0 h-0 border-l-[5px] border-r-[5px] border-b-[9px] border-transparent border-b-blue-600 flex-shrink-0"></div>',
                    'Ookla drive test (triangle)');
                if (ls.sig100_120) driveRows += _legendRow(
                    '<div class="w-3 h-3 rounded-full bg-yellow-400 border border-yellow-600 flex-shrink-0"></div>',
                    'RSRP −100 to −120 dBm');
                if (ls.sig121_130) driveRows += _legendRow(
                    '<div class="w-3 h-3 rounded-full bg-orange-500 border border-orange-700 flex-shrink-0"></div>',
                    'RSRP −121 to −130 dBm');
                if (ls.sig131_worse) driveRows += _legendRow(
                    '<div class="w-3 h-3 rounded-full bg-red-600 border border-red-800 flex-shrink-0"></div>',
                    'RSRP ≤ −131 dBm');
                if (ls.noise) driveRows += _legendRow(
                    '<div class="w-2.5 h-2.5 rounded-full bg-black border border-white flex-shrink-0"></div>',
                    'Noise / unclustered (−1)');
                var clusters = window._driveTestLegendClusters || [];
                if (clusters.length) {
                    driveRows += '<p class="text-[9px] text-gray-500 mb-1 mt-1">Point colour = cluster ID:</p>';
                    var maxShow = 8;
                    clusters.slice(0, maxShow).forEach(function (c) {
                        driveRows += _legendRow(
                            '<div class="w-3 h-3 rounded-sm flex-shrink-0 border border-white/80" style="background:' + c.color + ';"></div>',
                            'Cluster ' + c.id);
                    });
                    if (clusters.length > maxShow) {
                        driveRows += '<p class="text-[9px] text-gray-400 mb-1">+' + (clusters.length - maxShow) + ' more clusters</p>';
                    }
                }
                html += _legendSection('Drive Test Signal', driveRows);
            }

            if (ls.heatmap) {
                html += _legendSection('Analytics', _legendRow(
                    '<div class="w-20 h-2 rounded bg-gradient-to-r from-blue-400 via-yellow-400 to-red-600 flex-shrink-0"></div>',
                    'Congestion heatmap'));
            }

            if (typeof atomHullLayerJeak !== 'undefined' && atomHullLayerJeak && atomHullsOnJeak) {
                html += _legendSection('ATOM', _legendRow(
                    '<div class="w-4 h-3 rounded border-2 border-indigo-600 flex-shrink-0" style="background:rgba(99,102,241,0.35);"></div>',
                    'Signal cluster hull'));
                if (typeof atomPointsOnJeak !== 'undefined' && atomPointsOnJeak) {
                    html += _legendRow(
                        '<div class="w-2.5 h-2.5 rounded-full bg-indigo-500 border border-white flex-shrink-0"></div>',
                        'Cluster points (colour = cluster)');
                }
            }

            var gsActive = (typeof geoserverWmsEntries !== 'undefined' ? geoserverWmsEntries : [])
                .filter(function (e) { return e.active && e.layer; });
            var showCukai = gsActive.some(function (e) {
                var n = (e.layer.wmsParams && e.layer.wmsParams.layers) || '';
                return /cukai|permit|ktn_cukai/i.test(n);
            });
            var showExt = gsActive.some(function (e) {
                var n = (e.layer.wmsParams && e.layer.wmsParams.layers) || '';
                return /ext|extension|ktn_ext/i.test(n);
            });
            var showSigGs = gsActive.some(function (e) {
                var n = (e.layer.wmsParams && e.layer.wmsParams.layers) || '';
                return /signal|Random_Signal/i.test(n);
            });
            if (showCukai) {
                var b = '';
                b += _legendRow('<div class="w-3 h-3 flex-shrink-0 border border-green-700" style="background:#22c55e;"></div>', 'Paid');
                b += _legendRow('<div class="w-3 h-3 flex-shrink-0 border border-red-700" style="background:#ef4444;"></div>', 'Unpaid');
                b += _legendRow('<div class="w-3 h-3 flex-shrink-0 border border-blue-700" style="background:#3b82f6;"></div>', 'Exempted');
                html += _legendSection('Buildings (Cukai / Permit)', b);
            }
            if (showExt) {
                var ex = '';
                ex += _legendRow('<div class="w-3 h-3 flex-shrink-0 border border-gray-400" style="background:#e5e7eb;"></div>', 'Nil');
                ex += _legendRow('<div class="w-3 h-3 flex-shrink-0 border border-orange-500" style="background:#f97316;"></div>', 'Potential');
                html += _legendSection('Building Extension', ex);
            }
            if (showSigGs) {
                html += _legendSection('GeoServer Signal', _legendRow(
                    '<div class="w-3 h-3 rounded-full flex-shrink-0 border border-red-700" style="background:#e53e3e;"></div>',
                    'Signal sample points'));
            }

            el.innerHTML = html;
            if (emptyHint) emptyHint.classList.toggle('hidden', !!html);
        }

        function toggleMapLegend() {
            const body = document.getElementById('legendBody');
            const chevron = document.getElementById('legendChevron');
            const isHidden = body.style.display === 'none';
            body.style.display = isHidden ? 'block' : 'none';
            chevron.style.transform = isHidden ? 'rotate(0deg)' : 'rotate(-90deg)';
        }
        window.updateMapLegend = updateMapLegend;
