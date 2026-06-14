// =============================================================================
// ROLLOUT MODULE — Frontend JS
// =============================================================================
var _rolloutOpen  = false;
var _rolloutPlans = [];          // cached full list from server
var _rolloutCurrentPlan = null;  // currently open plan detail
var _rolloutNovaRunId   = null;  // set when launched from NOVA

function toggleRolloutPanel() {
    var p = document.getElementById('rolloutPanel');
    if (!p) return;
    _rolloutOpen = !_rolloutOpen;
    if (_rolloutOpen) {
        p.classList.add('open');
        rolloutLoadPlans();
    } else {
        p.classList.remove('open');
    }
    var btn = document.getElementById('rolloutBtn');
    if (btn) {
        btn.style.borderColor  = _rolloutOpen ? '#0369a1' : '#cbd5e1';
        btn.style.background   = _rolloutOpen ? '#eff6ff' : '#fff';
    }
}

// ── List view ──────────────────────────────────────────────────────────────
async function rolloutLoadPlans() {
    try {
        var res  = await fetch('/api/rollout/plans');
        var data = await res.json();
        _rolloutPlans = Array.isArray(data) ? data : [];
        rolloutRenderList(_rolloutPlans);
    } catch(e) {
        console.error('[Rollout] load plans error', e);
    }
}

function rolloutFilterList() {
    var q   = (document.getElementById('rolloutSearchInput')  || {value:''}).value.toLowerCase();
    var st  = (document.getElementById('rolloutStatusFilter') || {value:''}).value;
    var filtered = _rolloutPlans.filter(function(p) {
        var matchQ  = !q || p.np_id.toLowerCase().includes(q) || (p.site_name||'').toLowerCase().includes(q);
        var matchSt = !st || p.status === st;
        return matchQ && matchSt;
    });
    rolloutRenderList(filtered);
}

function rolloutRenderList(plans) {
    var container = document.getElementById('rolloutPlansList');
    if (!container) return;
    if (!plans.length) {
        container.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:32px 16px;">' +
            '<i class="fas fa-route" style="font-size:2rem;margin-bottom:8px;display:block;opacity:.4;"></i>' +
            '<p style="font-size:0.75rem;font-weight:600;color:#374151;">No rollout plans found</p>' +
            '<p style="font-size:0.65rem;color:#9ca3af;margin-top:4px;">Create from NOVA candidate or click "+ New Plan"</p></div>';
        return;
    }
    var STATUS_COLORS = {Active:'#16a34a', Blocked:'#dc2626', Completed:'#0369a1'};
    container.innerHTML = plans.map(function(p) {
        var col = STATUS_COLORS[p.status] || '#64748b';
        var td  = p.target_date ? ' · Target: ' + p.target_date : '';
        var ov  = p.overdue ? ' <span style="background:#fee2e2;color:#dc2626;border-radius:3px;padding:1px 5px;font-size:0.58rem;font-weight:700;">OVERDUE</span>' : '';
        var nova = p.nova_candidate_label ? ' <span style="background:#f0fdf4;color:#15803d;border-radius:3px;padding:1px 5px;font-size:0.58rem;font-weight:700;">NOVA ' + p.nova_candidate_label + '</span>' : '';
        return '<div onclick="rolloutOpenPlan(\'' + p.np_id + '\')" ' +
            'style="background:white;border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;margin-bottom:6px;cursor:pointer;' +
            'border-left:4px solid ' + col + ';transition:box-shadow .15s;" ' +
            'onmouseover="this.style.boxShadow=\'0 2px 12px rgba(0,0,0,.1)\'" ' +
            'onmouseout="this.style.boxShadow=\'\'">' +
            '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:3px;">' +
            '<span style="font-size:0.75rem;font-weight:800;color:#0c4a6e;">' + (p.site_name||p.np_id) + '</span>' +
            '<span style="font-size:0.6rem;font-weight:700;color:' + col + ';background:' + col + '18;border-radius:4px;padding:2px 7px;">' + p.status + '</span>' +
            '</div>' +
            '<div style="font-size:0.62rem;color:#64748b;">' + p.np_id + td + ov + nova + '</div>' +
            '<div style="font-size:0.6rem;color:#94a3b8;margin-top:2px;">' + (p.current_cp||'—') + ' · ' + (p.region||'—') + '</div>' +
            '</div>';
    }).join('');
}

// ── Detail view ────────────────────────────────────────────────────────────
async function rolloutOpenPlan(np_id) {
    try {
        var res  = await fetch('/api/rollout/plans/' + np_id);
        var data = await res.json();
        if (data.error) { alert(data.error); return; }
        _rolloutCurrentPlan = data;
        rolloutShowDetail(data);
    } catch(e) {
        alert('Error loading plan: ' + e.message);
    }
}

function rolloutShowList() {
    document.getElementById('rolloutListView').style.display   = 'flex';
    document.getElementById('rolloutDetailView').style.display = 'none';
    _rolloutCurrentPlan = null;
}

function rolloutShowDetail(data) {
    var plan = data.plan;
    document.getElementById('rolloutListView').style.display   = 'none';
    document.getElementById('rolloutDetailView').style.display = 'flex';

    document.getElementById('rolloutDetailNpId').textContent    = plan.np_id;
    document.getElementById('rolloutDetailSiteName').textContent = plan.site_name || plan.np_id;
    var STATUS_COLORS = {Active:'#16a34a', Blocked:'#dc2626', Completed:'#0369a1'};
    var col = STATUS_COLORS[plan.status] || '#64748b';
    document.getElementById('rolloutDetailMeta').innerHTML =
        '<span style="color:' + col + ';font-weight:700;">' + plan.status + '</span>' +
        (plan.current_cp ? ' · ' + plan.current_cp : '') +
        (plan.region     ? ' · ' + plan.region : '') +
        (plan.target_date ? ' · Target: ' + plan.target_date : '');

    rolloutSwitchTab('checkpoints');
    rolloutRenderCheckpoints(data.checkpoints || []);
    rolloutRenderDocs(data.documents || []);
    rolloutRenderTeam(data.members || []);
    rolloutRenderActivity(data.events || []);
}

function rolloutSwitchTab(tabName) {
    ['checkpoints','docs','team','activity'].forEach(function(t) {
        var pane = document.getElementById('rolloutTab' + t.charAt(0).toUpperCase() + t.slice(1));
        if (pane) pane.style.display = t === tabName ? 'block' : 'none';
    });
    document.querySelectorAll('.rollout-tab').forEach(function(btn) {
        var active = btn.getAttribute('data-tab') === tabName;
        btn.style.borderBottomColor = active ? '#0369a1' : 'transparent';
        btn.style.color             = active ? '#0369a1' : '#64748b';
    });
}

// ── Checkpoints ────────────────────────────────────────────────────────────
function rolloutRenderCheckpoints(cps) {
    var container = document.getElementById('rolloutTabCheckpoints');
    if (!container) return;
    var STATUS_STYLE = {
        Approved: 'background:#dcfce7;color:#16a34a;',
        Rejected: 'background:#fee2e2;color:#dc2626;',
        Pending:  'background:#f1f5f9;color:#64748b;',
    };
    var phases = {};
    cps.forEach(function(cp) {
        if (!phases[cp.phase]) phases[cp.phase] = [];
        phases[cp.phase].push(cp);
    });
    var demoHint = '<div style="font-size:0.62rem;color:#0369a1;background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:8px 10px;margin-bottom:10px;line-height:1.35;"><i class="fas fa-info-circle"></i> <strong>Demo:</strong> every checkpoint can be approved without uploading documents.</div>';
    container.innerHTML = demoHint + Object.keys(phases).map(function(ph) {
        return '<div style="margin-bottom:10px;">' +
            '<div style="font-size:0.6rem;font-weight:800;color:#0369a1;text-transform:uppercase;margin-bottom:4px;padding:4px 0;border-bottom:1px solid #bae6fd;">' + ph + '</div>' +
            phases[ph].map(function(cp) {
                var st   = cp.status || 'Pending';
                var stSt = STATUS_STYLE[st] || STATUS_STYLE.Pending;
                var icon = st === 'Approved' ? 'fa-check-circle' : (st === 'Rejected' ? 'fa-times-circle' : 'fa-circle');
                var iconCol = st === 'Approved' ? '#16a34a' : (st === 'Rejected' ? '#dc2626' : '#cbd5e1');
                var np_id = _rolloutCurrentPlan && _rolloutCurrentPlan.plan ? _rolloutCurrentPlan.plan.np_id : '';
                var actionBtns = '';
                if (st === 'Pending' || st === 'Rejected') {
                    actionBtns = '<button onclick="rolloutCheckpointAction(\'' + np_id + '\',\'' + cp.cp_code + '\',\'approve\')" ' +
                        'style="font-size:0.6rem;font-weight:700;background:#dcfce7;color:#16a34a;border:1px solid #86efac;border-radius:5px;padding:2px 8px;cursor:pointer;margin-left:4px;">Approve</button>' +
                        (st === 'Pending' ? '<button onclick="rolloutCheckpointAction(\'' + np_id + '\',\'' + cp.cp_code + '\',\'reject\')" ' +
                        'style="font-size:0.6rem;font-weight:700;background:#fee2e2;color:#dc2626;border:1px solid #fca5a5;border-radius:5px;padding:2px 8px;cursor:pointer;margin-left:4px;">Reject</button>' : '') +
                        (st === 'Rejected' ? '<button onclick="rolloutCheckpointAction(\'' + np_id + '\',\'' + cp.cp_code + '\',\'reopen\')" ' +
                        'style="font-size:0.6rem;font-weight:700;background:#f1f5f9;color:#64748b;border:1px solid #e2e8f0;border-radius:5px;padding:2px 8px;cursor:pointer;margin-left:4px;">Reopen</button>' : '');
                }
                return '<div style="display:flex;align-items:center;gap:6px;padding:6px 4px;border-bottom:1px solid #f1f5f9;">' +
                    '<i class="fas ' + icon + '" style="color:' + iconCol + ';font-size:0.85rem;flex-shrink:0;"></i>' +
                    '<div style="flex:1;">' +
                    '<div style="font-size:0.68rem;font-weight:700;color:#1e293b;">' + cp.cp_code + ' — ' + cp.activity + '</div>' +
                    (cp.notes ? '<div style="font-size:0.6rem;color:#64748b;">' + cp.notes + '</div>' : '') +
                    (cp.rejected_reason ? '<div style="font-size:0.6rem;color:#dc2626;">Reason: ' + cp.rejected_reason + '</div>' : '') +
                    '</div>' +
                    '<span style="font-size:0.58rem;font-weight:700;padding:2px 6px;border-radius:4px;' + stSt + '">' + st + '</span>' +
                    actionBtns +
                    '</div>';
            }).join('') +
            '</div>';
    }).join('');
}

async function rolloutCheckpointAction(np_id, cp_code, action) {
    var reason = '';
    var notes  = '';
    if (action === 'reject') {
        reason = prompt('Reason for rejection (required):');
        if (!reason) return;
    } else if (action === 'approve') {
        notes = prompt('Approval notes (optional):') || '';
    }
    try {
        var res  = await fetch('/api/rollout/plans/' + np_id + '/checkpoint/' + cp_code, {
            method:  'POST',
            headers: {'Content-Type': 'application/json'},
            body:    JSON.stringify({action: action, notes: notes, reason: reason}),
        });
        var data = await res.json();
        if (data.error) { alert(data.error); return; }
        await rolloutOpenPlan(np_id);
        if (typeof loadAnnotations === 'function') { await loadAnnotations(); }
    } catch(e) {
        alert('Error: ' + e.message);
    }
}

// ── Documents ──────────────────────────────────────────────────────────────
function rolloutRenderDocs(docs) {
    var container = document.getElementById('rolloutTabDocs');
    if (!container) return;
    var np_id = _rolloutCurrentPlan && _rolloutCurrentPlan.plan ? _rolloutCurrentPlan.plan.np_id : '';
    var uploadForm = '<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
        '<div style="font-size:0.65rem;font-weight:700;color:#0369a1;margin-bottom:6px;">Upload Document</div>' +
        '<div style="display:flex;flex-direction:column;gap:6px;">' +
        '<select id="rdCpCode" style="border:1px solid #bae6fd;border-radius:6px;padding:5px 8px;font-size:0.68rem;">' +
        ['CP/MS-1.0','CP/MS-1.1','CP/MS-1.2','CP/MS-2.0','CP/MS-2.1','CP/MS-2.2','CP/MS-2.3','CP/MS-2.4','CP/MS-2.5',
         'CP/MS-3.0','CP/MS-3.1','CP/MS-3.2','CP/MS-3.3','CP/MS-3.4','CP/MS-3.5','CP/MS-3.6','CP/MS-3.7','CP/MS-3.8','CP/MS-3.9','CP/MS-3.10']
        .map(function(c){return '<option>' + c + '</option>';}).join('') +
        '</select>' +
        '<input id="rdDesc" type="text" placeholder="Description" style="border:1px solid #bae6fd;border-radius:6px;padding:5px 8px;font-size:0.68rem;"/>' +
        '<input id="rdFile" type="file" style="font-size:0.65rem;"/>' +
        '<button onclick="rolloutUploadDoc(\'' + np_id + '\')" style="background:linear-gradient(135deg,#0c4a6e,#0369a1);color:white;border:none;border-radius:6px;padding:6px 12px;font-size:0.68rem;font-weight:700;cursor:pointer;">Upload</button>' +
        '</div></div>';

    if (!docs.length) {
        container.innerHTML = uploadForm + '<div style="text-align:center;color:#9ca3af;padding:16px;font-size:0.72rem;">No documents uploaded yet.</div>';
        return;
    }
    container.innerHTML = uploadForm + docs.map(function(d) {
        var size = d.file_size > 1024 ? Math.round(d.file_size/1024) + ' KB' : d.file_size + ' B';
        return '<div style="display:flex;align-items:center;gap:8px;padding:7px 4px;border-bottom:1px solid #f1f5f9;">' +
            '<i class="fas fa-file-alt" style="color:#0369a1;flex-shrink:0;"></i>' +
            '<div style="flex:1;">' +
            '<div style="font-size:0.68rem;font-weight:600;color:#1e293b;">' + d.filename + '</div>' +
            '<div style="font-size:0.6rem;color:#64748b;">' + (d.cp_code||'—') + ' · ' + size + (d.description ? ' · ' + d.description : '') + '</div>' +
            '</div>' +
            '<a href="/api/rollout/plans/' + np_id + '/documents/' + d.id + '" target="_blank" ' +
            'style="font-size:0.6rem;color:#0369a1;font-weight:700;text-decoration:none;">Download</a>' +
            '</div>';
    }).join('');
}

async function rolloutUploadDoc(np_id) {
    var fileInput = document.getElementById('rdFile');
    var cpCode    = (document.getElementById('rdCpCode') || {}).value || '';
    var desc      = (document.getElementById('rdDesc')   || {}).value || '';
    if (!fileInput || !fileInput.files.length) { alert('Select a file first.'); return; }
    var fd = new FormData();
    fd.append('file', fileInput.files[0]);
    fd.append('cp_code', cpCode);
    fd.append('description', desc);
    try {
        var res  = await fetch('/api/rollout/plans/' + np_id + '/documents', {method:'POST', body:fd});
        var data = await res.json();
        if (data.error) { alert(data.error); return; }
        rolloutOpenPlan(np_id);
    } catch(e) {
        alert('Upload error: ' + e.message);
    }
}

// ── Team ───────────────────────────────────────────────────────────────────
function rolloutRenderTeam(members) {
    var container = document.getElementById('rolloutTabTeam');
    if (!container) return;
    var np_id = _rolloutCurrentPlan && _rolloutCurrentPlan.plan ? _rolloutCurrentPlan.plan.np_id : '';
    var addRow = '<div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:10px 12px;margin-bottom:10px;">' +
        '<div style="font-size:0.65rem;font-weight:700;color:#0369a1;margin-bottom:6px;">Add Team Member</div>' +
        '<div style="display:flex;gap:6px;">' +
        '<input id="rtUserId" type="number" placeholder="User ID" style="width:80px;border:1px solid #bae6fd;border-radius:6px;padding:5px 8px;font-size:0.68rem;"/>' +
        '<select id="rtRole" style="flex:1;border:1px solid #bae6fd;border-radius:6px;padding:5px 8px;font-size:0.68rem;">' +
        ['Project Manager','USPD Approver','State Office Approver','Site Engineer','Sub-Con','NOC Engineer','DUSP Approver','Observer']
        .map(function(r){return '<option>' + r + '</option>';}).join('') +
        '</select>' +
        '<button onclick="rolloutAddMember(\'' + np_id + '\')" style="background:linear-gradient(135deg,#0c4a6e,#0369a1);color:white;border:none;border-radius:6px;padding:5px 12px;font-size:0.68rem;font-weight:700;cursor:pointer;">Add</button>' +
        '</div></div>';

    if (!members.length) {
        container.innerHTML = addRow + '<div style="text-align:center;color:#9ca3af;padding:16px;font-size:0.72rem;">No team members assigned.</div>';
        return;
    }
    container.innerHTML = addRow + members.map(function(m) {
        return '<div style="display:flex;align-items:center;gap:8px;padding:7px 4px;border-bottom:1px solid #f1f5f9;">' +
            '<div style="width:28px;height:28px;background:#0369a1;border-radius:50%;display:flex;align-items:center;justify-content:center;color:white;font-size:0.7rem;font-weight:700;flex-shrink:0;">' +
            (m.full_name || m.username || 'U').charAt(0).toUpperCase() + '</div>' +
            '<div style="flex:1;">' +
            '<div style="font-size:0.7rem;font-weight:600;color:#1e293b;">' + (m.full_name || m.username || 'User ' + m.user_id) + '</div>' +
            '<div style="font-size:0.6rem;color:#64748b;">' + m.rollout_role + '</div>' +
            '</div>' +
            '<button onclick="rolloutRemoveMember(\'' + np_id + '\',' + m.user_id + ')" ' +
            'style="font-size:0.6rem;color:#dc2626;background:none;border:none;cursor:pointer;">Remove</button>' +
            '</div>';
    }).join('');
}

async function rolloutAddMember(np_id) {
    var uid  = parseInt((document.getElementById('rtUserId') || {}).value);
    var role = (document.getElementById('rtRole') || {}).value;
    if (!uid) { alert('Enter a valid User ID'); return; }
    try {
        var res  = await fetch('/api/rollout/plans/' + np_id + '/members', {
            method:  'POST',
            headers: {'Content-Type':'application/json'},
            body:    JSON.stringify({user_id: uid, rollout_role: role}),
        });
        var data = await res.json();
        if (data.error) { alert(data.error); return; }
        rolloutOpenPlan(np_id);
    } catch(e) { alert('Error: ' + e.message); }
}

async function rolloutRemoveMember(np_id, uid) {
    if (!confirm('Remove this member?')) return;
    try {
        var res  = await fetch('/api/rollout/plans/' + np_id + '/members/' + uid, {method:'DELETE'});
        var data = await res.json();
        if (data.error) { alert(data.error); return; }
        rolloutOpenPlan(np_id);
    } catch(e) { alert('Error: ' + e.message); }
}

// ── Activity log ───────────────────────────────────────────────────────────
function rolloutRenderActivity(events) {
    var container = document.getElementById('rolloutTabActivity');
    if (!container) return;
    if (!events.length) {
        container.innerHTML = '<div style="text-align:center;color:#9ca3af;padding:16px;font-size:0.72rem;">No activity yet.</div>';
        return;
    }
    container.innerHTML = events.map(function(ev) {
        var dt = ev.created_at ? ev.created_at.replace('T',' ').substring(0,16) : '';
        return '<div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid #f1f5f9;">' +
            '<i class="fas fa-circle" style="color:#bae6fd;font-size:0.45rem;margin-top:5px;flex-shrink:0;"></i>' +
            '<div>' +
            '<div style="font-size:0.68rem;font-weight:600;color:#1e293b;">' + ev.event_type + (ev.cp_code ? ' <span style="color:#0369a1;font-size:0.6rem;">' + ev.cp_code + '</span>' : '') + '</div>' +
            (ev.note ? '<div style="font-size:0.6rem;color:#475569;">' + ev.note + '</div>' : '') +
            '<div style="font-size:0.58rem;color:#94a3b8;">' + (ev.username||'system') + ' · ' + dt + '</div>' +
            '</div></div>';
    }).join('');
}

// ── Create plan modal ──────────────────────────────────────────────────────
async function rolloutOpenCreate(prefill) {
    prefill = prefill || {};
    var modal = document.getElementById('rolloutCreateModal');
    if (!modal) return;
    document.getElementById('rcSiteName').value      = prefill.site_name      || '';
    document.getElementById('rcLat').value           = prefill.lat            || '';
    document.getElementById('rcLon').value           = prefill.lon            || '';
    document.getElementById('rcNovaRunId').value     = prefill.nova_run_id    || '';
    document.getElementById('rcNovaLabel').value     = prefill.nova_label     || '';
    document.getElementById('rcAtomClusterId').value = prefill.atom_cluster_id || '';
    document.getElementById('rcTriggerRef').value    = prefill.trigger_ref    || '';
    if (prefill.trigger_type) {
        var sel = document.getElementById('rcTriggerType');
        for (var i=0; i<sel.options.length; i++) {
            if (sel.options[i].value === prefill.trigger_type) { sel.selectedIndex = i; break; }
        }
    }
    // Show pre-assigned NP-id (from ATOM cluster) or fetch next from server
    var npIdDisplay = document.getElementById('rcNpIdDisplay');
    var rcNpId      = document.getElementById('rcNpId');
    if (prefill.np_id) {
        rcNpId.value = prefill.np_id;
        if (npIdDisplay) npIdDisplay.textContent = prefill.np_id;
    } else {
        rcNpId.value = '';
        if (npIdDisplay) npIdDisplay.textContent = '…';
        try {
            var r = await fetch('/api/rollout/next_np_id');
            var d = await r.json();
            if (npIdDisplay) npIdDisplay.textContent = d.next_np_id || '(auto)';
        } catch(_) {
            if (npIdDisplay) npIdDisplay.textContent = '(auto)';
        }
    }
    modal.style.display = 'flex';
}

function rolloutCloseCreate() {
    var modal = document.getElementById('rolloutCreateModal');
    if (modal) modal.style.display = 'none';
}

async function rolloutSubmitCreate(e) {
    e.preventDefault();
    var payload = {
        site_name:             document.getElementById('rcSiteName').value.trim(),
        trigger_type:          document.getElementById('rcTriggerType').value,
        trigger_ref:           document.getElementById('rcTriggerRef').value.trim(),
        region:                document.getElementById('rcRegion').value.trim(),
        zone:                  document.getElementById('rcZone').value.trim(),
        objective:             document.getElementById('rcObjective').value.trim(),
        intended_lat:          parseFloat(document.getElementById('rcLat').value),
        intended_lon:          parseFloat(document.getElementById('rcLon').value),
        target_date:           document.getElementById('rcTargetDate').value || null,
        nova_run_id:           parseInt(document.getElementById('rcNovaRunId').value) || null,
        nova_candidate_label:  document.getElementById('rcNovaLabel').value || null,
        atom_cluster_id:       parseInt(document.getElementById('rcAtomClusterId').value) || null,
        np_id:                 document.getElementById('rcNpId').value || null,
    };
    if (!payload.site_name || isNaN(payload.intended_lat) || isNaN(payload.intended_lon)) {
        alert('Site name and valid coordinates are required.'); return;
    }
    try {
        var res  = await fetch('/api/rollout/plans', {
            method:  'POST',
            headers: {'Content-Type':'application/json'},
            body:    JSON.stringify(payload),
        });
        var data = await res.json();
        if (data.error) { alert(data.error); return; }
        rolloutCloseCreate();
        if (!_rolloutOpen) toggleRolloutPanel();
        else { rolloutLoadPlans(); }
        alert('Rollout plan ' + data.np_id + ' created successfully.');
    } catch(e) {
        alert('Error: ' + e.message);
    }
}

// ── NOVA → Rollout quick-launch ────────────────────────────────────────────
function rolloutCreateFromNova(lat, lng, label) {
    rolloutOpenCreate({
        lat:          lat,
        lon:          lng,
        nova_run_id:  _novaRunId || null,
        nova_label:   label,
        trigger_type: 'NOVA Candidate',
        site_name:    'NOVA Candidate ' + label,
        np_id:        null,  // always auto-generate for NOVA candidates
    });
    if (!_rolloutOpen) toggleRolloutPanel();
}

// Close modal on backdrop click
document.addEventListener('click', function(e) {
    var modal = document.getElementById('rolloutCreateModal');
    if (modal && modal.style.display === 'flex' && e.target === modal) rolloutCloseCreate();
});
