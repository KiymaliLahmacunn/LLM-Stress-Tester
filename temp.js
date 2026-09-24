
        // --- Tab Switching ---
        function switchTab(tabId) {
            const tabs = ['test', 'results', 'datasets'];
            
            tabs.forEach(t => {
                const tabEl = document.getElementById('tab-' + t);
                const btnEl = document.getElementById('tab-btn-' + t);
                if (tabEl) {
                    tabEl.classList.remove('opacity-100', 'pointer-events-auto');
                    tabEl.classList.add('opacity-0', 'pointer-events-none');
                }
                if (btnEl) {
                    btnEl.className = 'px-4 py-1.5 rounded-md text-sm font-medium text-gray-400 hover:text-gray-200 transition-all';
                }
            });
            
            const activeClass = 'px-4 py-1.5 rounded-md text-sm font-medium bg-gray-800 text-cyan-400 shadow transition-all';
            
            setTimeout(() => {
                const targetTab = document.getElementById('tab-' + tabId);
                const targetBtn = document.getElementById('tab-btn-' + tabId);
                if (targetTab) {
                    targetTab.classList.remove('opacity-0', 'pointer-events-none');
                    targetTab.classList.add('opacity-100', 'pointer-events-auto');
                }
                if (targetBtn) {
                    targetBtn.className = activeClass;
                }
            }, 50);
        }
        


        // --- Dataset Management ---
        async function fetchDatasets(selectedFilename = null) {
            try {
                const res = await fetch('/api/datasets');
                const data = await res.json();
                const sel = document.getElementById('dataset-select');
                sel.innerHTML = '';
                if (!data.datasets || data.datasets.length === 0) {
                    sel.innerHTML = '<option value="">No datasets found</option>';
                    return;
                }
                data.datasets.forEach(d => {
                    const opt = document.createElement('option');
                    opt.value = d.name; // Now returns objects
                    opt.textContent = d.name;
                    sel.appendChild(opt);
                });
                if (selectedFilename) sel.value = selectedFilename;
            } catch (e) {
                console.error("Failed to load datasets:", e);
            }
        }

        async function loadDatasetsTab() {
            try {
                const res = await fetch('/api/datasets');
                const data = await res.json();
                const tbody = document.getElementById('datasets-tbody');
                tbody.innerHTML = '';
                if (!data.datasets || data.datasets.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-gray-500">No datasets found</td></tr>';
                    return;
                }
                
                data.datasets.forEach(d => {
                    const tr = document.createElement('tr');
                    tr.className = "hover:bg-gray-800/50 transition-colors";
                    tr.innerHTML = `
                        <td class="px-4 py-3 font-medium text-cyan-400">${d.name}</td>
                        <td class="px-4 py-3">${d.row_count !== undefined ? d.row_count : '-'}</td>
                        <td class="px-4 py-3">${d.created_at ? new Date(d.created_at).toLocaleString() : '-'}</td>
                        <td class="px-4 py-3">${d.updated_at ? new Date(d.updated_at).toLocaleString() : '-'}</td>
                        <td class="px-4 py-3 text-right">
                            <button onclick="downloadDataset('${d.name}')" class="px-2 py-1 bg-gray-800 hover:bg-gray-700 rounded text-indigo-400 mr-2 border border-gray-700">Download</button>
                            <button onclick="deleteDataset('${d.name}')" class="px-2 py-1 bg-gray-800 hover:bg-red-900/50 rounded text-red-400 border border-gray-700">Delete</button>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (e) {
                console.error("Failed to load datasets tab:", e);
            }
        }

        async function deleteDataset(filename) {
            if (!confirm(`Are you sure you want to delete ${filename}?`)) return;
            try {
                const res = await fetch(`/api/datasets/delete/${encodeURIComponent(filename)}`, { method: 'DELETE' });
                const data = await res.json();
                if (data.error) alert(data.error);
                else {
                    loadDatasetsTab();
                    fetchDatasets();
                }
            } catch(e) {
                alert("Delete failed: " + e);
            }
        }

        async function doUpload(file) {
            if (!file) return;
            // check size < 10MB
            if (file.size > 10 * 1024 * 1024) {
                alert("File exceeds 10MB limit.");
                return;
            }
            const formData = new FormData();
            formData.append('file', file);
            try {
                const res = await fetch('/api/datasets/upload', { method: 'POST', body: formData });
                const data = await res.json();
                if (data.error) alert(data.error);
                else {
                    await fetchDatasets(data.filename);
                    if (document.getElementById('tab-datasets').classList.contains('opacity-100')) {
                        loadDatasetsTab();
                    }
                }
            } catch(err) {
                alert("Upload failed: " + err);
            }
        }

        async function uploadDataset(e) {
            const file = e.target.files[0];
            await doUpload(file);
            e.target.value = '';
        }

        async function uploadDatasetTab(e) {
            const file = e.target.files[0];
            await doUpload(file);
            e.target.value = '';
        }

        function downloadDataset(overrideFilename = null) {
            let dsFilename = overrideFilename;
            if (typeof overrideFilename !== 'string') {
                dsFilename = document.getElementById('dataset-select').value;
            }
            if (!dsFilename) return alert("No dataset selected.");
            window.location.href = `/api/datasets/download/${encodeURIComponent(dsFilename)}`;
        }

        // --- Live Chart Setup ---
        const ctx = document.getElementById('liveChart').getContext('2d');
        const liveChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [
                    { label: 'Avg TTFT (ms)', data: [], borderColor: '#10b981', backgroundColor: 'rgba(16, 185, 129, 0.1)', borderWidth: 2, fill: true, pointRadius: 0, tension: 0.4 },
                    { label: 'Avg Gen (ms)', data: [], borderColor: '#3b82f6', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 0, tension: 0.4 },
                    { label: 'Avg Total (ms)', data: [], borderColor: '#f43f5e', backgroundColor: 'transparent', borderWidth: 2, pointRadius: 0, tension: 0.4 }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { intersect: false, mode: 'index' },
                plugins: { legend: { display: true, labels: { color: '#9ca3af', boxWidth: 12, font: {size: 10} } } },
                scales: {
                    x: { display: false },
                    y: { grid: { color: '#374151', borderDash: [2, 4] }, ticks: { color: '#9ca3af', font: {size: 10} }, beginAtZero: true }
                }
            }
        });

        // --- Test Control & Polling ---
        let isTesting = false;
        let statusInterval = null;
        let lastLogLine = 0;

        function updateUIState(running, progress = 0) {
            isTesting = running;
            const btn = document.getElementById('btn-start');
            const overlay = document.getElementById('test-status-overlay');
            const spinner = document.getElementById('test-status-spinner');
            const statusTxt = document.getElementById('test-status-text');
            const progBar = document.getElementById('progress-bar');
            
            if (running) {
                btn.innerHTML = 'STOP TEST ABORT';
                btn.className = "w-full mt-4 py-3.5 rounded-lg text-sm font-bold tracking-widest text-white bg-gradient-to-r from-red-600 to-rose-700 hover:from-red-500 hover:to-rose-600 transition-all uppercase shadow-lg shadow-red-500/20 pulse-glow";
                
                overlay.classList.remove('opacity-100');
                overlay.classList.add('opacity-0', 'pointer-events-none');
                progBar.style.width = Math.min(100, progress * 100) + '%';
            } else {
                btn.innerHTML = 'START STRESS TEST';
                btn.className = "w-full mt-4 py-3.5 rounded-lg text-sm font-bold tracking-widest text-white bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 transition-all uppercase shadow-lg hover:shadow-cyan-500/30";
                
                overlay.classList.remove('opacity-0', 'pointer-events-none');
                overlay.classList.add('opacity-100');
                spinner.classList.add('hidden');
                statusTxt.innerText = 'SYSTEM IDLE';
                progBar.style.width = '0%';
            }
        }

        async function toggleTest() {
            if (isTesting) {
                await fetch('/api/test/stop', { method: 'POST' });
                updateUIState(false);
            } else {
                const finalUrl = document.getElementById('custom-url').value.trim();
                const finalName = document.getElementById('custom-model-name').value.trim() || "Custom";
                const dsFilename = document.getElementById('dataset-select').value;
                
                if (!finalUrl || !dsFilename) return alert("Please enter a Target URL and select a Dataset.");
                
                document.getElementById('log-content-left').innerHTML = ''; document.getElementById('log-content-right').innerHTML = ''; // clear logs
                lastLogLine = 0;
                liveChart.data.labels = [];
                liveChart.data.datasets.forEach(ds => ds.data = []);
                liveChart.update();
                
                document.getElementById('test-status-text').innerText = 'INITIALIZING...';
                document.getElementById('test-status-spinner').classList.remove('hidden');

                const seedEl = document.getElementById("seed-value");
                const useSeed = document.getElementById("seed-toggle").checked;
                const seedVal = useSeed ? parseInt(seedEl.value) : null;
                const timeoutEl = document.getElementById("timeout");
                const timeoutVal = timeoutEl ? parseInt(timeoutEl.value) : 30;

                const payload = {
                    model_name: finalName,
                    model_url: finalUrl,
                    dataset_filename: dsFilename,
                    concurrency: parseInt(document.getElementById("concurrency").value),
                    rps: parseInt(document.getElementById("rps").value),
                    duration: parseInt(document.getElementById("duration").value),
                    thinking: document.getElementById("thinking-mode").checked,
                    streaming: document.getElementById("streaming-mode").checked,
                    hardware: document.getElementById('custom-hardware') ? document.getElementById('custom-hardware').value : "",
                    seed: seedVal,
                    timeout: timeoutVal
                };
                
                const res = await fetch('/api/test/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                
                const data = await res.json();
                if (data.error) {
                    alert("Test Failed to Start: " + data.error);
                    updateUIState(false);
                    return;
                }
                
                updateUIState(true, 0);
                startStatusPolling();
            }
        }

        function startStatusPolling() {
            if (statusInterval) clearInterval(statusInterval);
            statusInterval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/test/status?last_log_idx=${lastLogLine}`);
                    const data = await res.json();
                    
                    document.getElementById('stat-active').innerText = data.active_workers;
                    document.getElementById('stat-total').innerText = data.total_requests;
                    document.getElementById('stat-success').innerText = data.success_count;
                    document.getElementById('stat-failed').innerText = data.fail_count;
                    document.getElementById('stat-rps').innerText = data.avg_rps.toFixed(2);
                    document.getElementById('stat-tps').innerText = data.avg_tps.toFixed(2);
                    
                    if (data.is_testing) {
                        updateUIState(true, data.progress);
                        liveChart.data.labels.push('');
                        liveChart.data.datasets[0].data.push(data.avg_ttft_ms);
                        liveChart.data.datasets[1].data.push(data.avg_generation_ms);
                        liveChart.data.datasets[2].data.push(data.avg_total_ms);
                        if (liveChart.data.labels.length > 30) {
                            liveChart.data.labels.shift();
                            liveChart.data.datasets.forEach(ds => ds.data.shift());
                        }
                        liveChart.update();
                    } else {
                        updateUIState(false);
                        clearInterval(statusInterval);
                        statusInterval = null;
                    }
                    
                    if (data.new_logs && data.new_logs.length > 0) {
                        const logLeft = document.getElementById('log-content-left');
                        const logRight = document.getElementById('log-content-right');
                        
                        data.new_logs.forEach(line => {
                            const div = document.createElement('div');
                            div.textContent = line;
                            div.className = "text-gray-300 mb-2 border-b border-gray-800 pb-1";
                            
                            if (line.includes(">>> PROMPT")) {
                                div.className = "text-cyan-400 mb-2 border-b border-gray-800 pb-1";
                                logLeft.appendChild(div);
                            } else if (line.includes("--- RESPONSE")) {
                                div.className = "mb-4 border-b border-gray-800 pb-2 font-mono text-[11px] leading-relaxed text-gray-300";
                                
                                let formatted = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                                
                                // Syntax highlighting mimicking LM Studio
                                formatted = formatted.replace(/(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/g, '<span class="text-blue-400">$1</span>');
                                formatted = formatted.replace(/\[DEBUG\]/g, '<span class="text-blue-500 font-bold">[DEBUG]</span>');
                                formatted = formatted.replace(/\[INFO\]/g, '<span class="text-emerald-500 font-bold">[INFO]</span>');
                                formatted = formatted.replace(/(\".*?\"):/g, '<span class="text-emerald-400">$1</span>:');
                                formatted = formatted.replace(/: (\".*?\")/g, ': <span class="text-emerald-300">$1</span>');
                                formatted = formatted.replace(/--- RESPONSE.*?---/, '<span class="text-gray-500 italic">$&</span>');
                                
                                div.innerHTML = formatted;
                                logRight.appendChild(div);
                            } else {
                                div.className = "text-gray-500 italic mb-2";
                                const div2 = div.cloneNode(true);
                                logLeft.appendChild(div);
                                logRight.appendChild(div2);
                            }
                        });
                        lastLogLine = data.last_log_idx;
                        const cLeft = document.getElementById('log-container-left');
                        const cRight = document.getElementById('log-container-right');
                        cLeft.scrollTop = cLeft.scrollHeight;
                        cRight.scrollTop = cRight.scrollHeight;
                    }
                } catch (e) {
                    console.error("Polling error", e);
                }
            }, 300);
        }

        // --- Results Loading ---
        
        
        async function testConnection() {
            const url = document.getElementById('custom-url').value;
            const dot = document.getElementById('conn-dot');
            const txt = document.getElementById('conn-text');
            const stat = document.getElementById('conn-status');
            
            // Loading state
            dot.className = "w-2 h-2 rounded-full bg-yellow-400 shadow-[0_0_8px_rgba(250,204,21,0.6)] animate-pulse";
            txt.innerText = "Connecting...";
            stat.className = "text-xs text-yellow-400 flex items-center gap-1 font-medium transition-colors";
            
            try {
                const res = await fetch('/api/system/test_connection?url=' + encodeURIComponent(url));
                const data = await res.json();
                
                if (data.status === "ok") {
                    // Update Model name
                    if (data.model) {
                        const mInput = document.getElementById('custom-model-name');
                        mInput.value = data.model;
                        mInput.classList.add('ring-2', 'ring-emerald-500', 'border-emerald-500');
                        setTimeout(() => {
                            mInput.classList.remove('ring-2', 'ring-emerald-500', 'border-emerald-500');
                        }, 800);
                    }
                    
                    // Check prompt test result
                    if (data.prompt_ok) {
                        if (data.is_text_model) {
                            // Full success: text-to-text model confirmed
                            dot.className = "w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]";
                            txt.innerHTML = 'Connected ✓ Prompt ✓ <span class="bg-emerald-500/20 text-emerald-300 px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wider border border-emerald-500/30">TXT</span>';
                            stat.className = "text-xs text-emerald-400 flex items-center gap-1 font-medium transition-colors";
                        } else {
                            // Prompt OK but model might not be pure text (embedding etc.)
                            dot.className = "w-2 h-2 rounded-full bg-amber-400 shadow-[0_0_8px_rgba(245,158,11,0.6)]";
                            txt.innerHTML = 'Connected ✓ <span class="bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wider border border-amber-500/30">TXT?</span>';
                            stat.className = "text-xs text-amber-400 flex items-center gap-1 font-medium transition-colors";
                        }
                    } else {
                        // Model found but prompt failed
                        dot.className = "w-2 h-2 rounded-full bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.6)]";
                        let errorMsg = "No response!";
                        if (data.prompt_error) {
                            errorMsg = data.prompt_error;
                            console.warn("Prompt test error:", data.prompt_error);
                        }
                        txt.innerText = "Model found but error: " + errorMsg;
                        stat.className = "text-xs text-red-400 flex items-center gap-1 font-medium transition-colors";
                    }
                    
                    // Also update hardware automatically
                    try {
                        const hwRes = await fetch('/api/system/hardware');
                        const hwData = await hwRes.json();
                        if (hwData && hwData.hardware) {
                            const hwInput = document.getElementById('custom-hardware');
                            hwInput.value = hwData.hardware;
                            hwInput.classList.add('ring-2', 'ring-emerald-500', 'border-emerald-500');
                            setTimeout(() => {
                                hwInput.classList.remove('ring-2', 'ring-emerald-500', 'border-emerald-500');
                            }, 800);
                        }
                    } catch (e) {
                        console.error("Hardware fetch failed:", e);
                    }
                    
                } else {
                    // Connection error
                    dot.className = "w-2 h-2 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]";
                    txt.innerText = "Connection Error!";
                    stat.className = "text-xs text-red-500 flex items-center gap-1 font-medium transition-colors";
                    console.error(data.error);
                }
            } catch (err) {
                dot.className = "w-2 h-2 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]";
                txt.innerText = "Server Error";
                stat.className = "text-xs text-red-500 flex items-center gap-1 font-medium transition-colors";
            }
        }

async function showReport(filename) {
    const modal = document.getElementById('report-modal');
    const loading = document.getElementById('report-loading');
    const errDiv = document.getElementById('report-error');
    const content = document.getElementById('report-content');
    
    document.getElementById('report-filename').innerText = decodeURIComponent(filename);
    
    modal.classList.remove('hidden');
    loading.classList.remove('hidden');
    errDiv.classList.add('hidden');
    content.classList.add('hidden');
    
    try {
        const res = await fetch('/api/results/report/' + filename);
        const data = await res.json();
        
        if (data.error) {
            errDiv.innerText = data.error;
            errDiv.classList.remove('hidden');
            loading.classList.add('hidden');
            return;
        }
        
        document.getElementById('rep-total').innerText = data.total_requests;
        document.getElementById('rep-success').innerText = data.successful_requests;
        document.getElementById('rep-failed').innerText = data.failed_requests;
        
        const tbody = document.getElementById('report-tbody');
        tbody.innerHTML = '';
        
        for (const [metric, stats] of Object.entries(data.metrics)) {
            const tr = document.createElement('tr');
            tr.className = "border-b border-gray-800/50 hover:bg-gray-800/30";
            tr.innerHTML = `
                <td class="p-3 font-medium text-gray-300">${metric}</td>
                <td class="p-3 text-right text-gray-400">${stats.n_ok}</td>
                <td class="p-3 text-right">${stats.mean}</td>
                <td class="p-3 text-right">${stats.median}</td>
                <td class="p-3 text-right text-gray-500">${stats.stdev}</td>
                <td class="p-3 text-right text-emerald-400">${stats.min}</td>
                <td class="p-3 text-right text-cyan-300 font-bold">${stats.p50}</td>
                <td class="p-3 text-right text-purple-300 font-bold">${stats.p90}</td>
                <td class="p-3 text-right text-pink-300 font-bold">${stats.p95}</td>
                <td class="p-3 text-right text-red-300 font-bold">${stats.p99}</td>
                <td class="p-3 text-right text-orange-400">${stats.max}</td>
            `;
            tbody.appendChild(tr);
        }
        
        content.classList.remove('hidden');
    } catch (err) {
        errDiv.innerText = "Connection error: " + err.message;
        errDiv.classList.remove('hidden');
    } finally {
        loading.classList.add('hidden');
    }
}

async function loadResults() {
    try {
        const res = await fetch('/api/results');
        const data = await res.json();
        const tbody = document.getElementById('results-tbody');
        tbody.innerHTML = '';
        
        if (data.files && data.files.length > 0) {
            data.files.forEach(item => {
                const dateObj = new Date(item.mtime * 1000);
                const dateStr = dateObj.toLocaleString();
                
                let modelStr = item.meta && item.meta.model ? item.meta.model : "-";
                let hwStr = item.meta && item.meta.hardware ? item.meta.hardware : "-";
                let datasetStr = item.meta && item.meta.dataset ? item.meta.dataset : "-";
                
                let dsFile = datasetStr;
                if (!dsFile.endsWith(".csv")) dsFile += ".csv";
                
                let thinkStr = item.meta && item.meta.thinking ? item.meta.thinking : "-";
                let streamStr = item.meta && item.meta.streaming ? item.meta.streaming : "-";
                
                const tr = document.createElement('tr');
                tr.className = "hover:bg-gray-800/50 transition-colors border-b border-gray-800 text-sm";
                tr.innerHTML = `
                    <td class="px-4 py-3 text-center">
                        <input type="checkbox" class="compare-cb form-checkbox text-emerald-500 rounded border-gray-700 bg-gray-900" value="${item.name}">
                    </td>
                    <td class="px-4 py-3 font-medium text-cyan-300">${modelStr}</td>
                    <td class="px-4 py-3 text-gray-400 text-xs">${hwStr}</td>
                    <td class="px-4 py-3 text-gray-400">
                        ${datasetStr}
                        <br/>
                        <button onclick="window.location.href='/api/datasets/download/' + encodeURIComponent('${dsFile}')" class="mt-1 text-xs text-blue-400 hover:text-blue-300 underline" title="Download dataset"> Download Dataset</button>
                    </td>
                    <td class="px-4 py-3 text-gray-400 text-center">${thinkStr === "On" ? '<span class="text-emerald-400">On</span>' : '<span class="text-gray-500">Off</span>'}</td>
                    <td class="px-4 py-3 text-gray-400 text-center">${item.meta && item.meta.seed !== undefined ? item.meta.seed : '-'}</td>
                    <td class="px-4 py-3 text-gray-400 text-right">${dateStr}</td>
                    <td class="px-4 py-3 text-right whitespace-nowrap">
                        <button onclick="showReport('${encodeURIComponent(item.name)}')" class="px-3 py-1 bg-purple-600 hover:bg-purple-500 text-white rounded transition-colors mr-2">Report</button>
                        <button onclick="window.location.href='/api/results/download/' + encodeURIComponent('${item.name}')" class="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded transition-colors">Download</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        } else {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-gray-500">No result files yet.</td></tr>';
        }
    } catch (err) {
        console.error("Failed to load results", err);
    }
}

async function compareSelected() {
    const checkboxes = document.querySelectorAll('.compare-cb:checked');
    const files = Array.from(checkboxes).map(cb => cb.value);
    
    if (files.length < 2) {
        alert('Please select at least 2 result files to compare.');
        return;
    }
    
    const modal = document.getElementById('compare-modal');
    const loading = document.getElementById('compare-loading');
    const errDiv = document.getElementById('compare-error');
    const content = document.getElementById('compare-content');
    
    modal.classList.remove('hidden');
    loading.classList.remove('hidden');
    errDiv.classList.add('hidden');
    content.classList.add('hidden');
    
    try {
        const res = await fetch('/api/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files })
        });
        const data = await res.json();
        
        if (data.error) {
            errDiv.innerText = data.error;
            errDiv.classList.remove('hidden');
            loading.classList.add('hidden');
            return;
        }
        
        const tbody = document.getElementById('compare-tbody');
        tbody.innerHTML = '';
        
        let headTr = document.getElementById('compare-thead-tr');
        if(!headTr) {
            const thead = document.getElementById('compare-thead');
            headTr = document.createElement('tr');
            headTr.id = 'compare-thead-tr';
            thead.appendChild(headTr);
        }
        if(headTr && data.comparisons.length > 0) {
            let hHtml = '<th class="p-3 text-left">File / Metric</th>';
            data.comparisons[0].headers.slice(1).forEach(h => {
                hHtml += `<th class="p-3 text-right">${h}</th>`;
            });
            headTr.innerHTML = hHtml;
        }

        data.comparisons.forEach(comp => {
            const titleTr = document.createElement('tr');
            titleTr.className = "bg-gray-800/80 border-b border-gray-700";
            titleTr.innerHTML = `<td colspan="${comp.headers.length}" class="p-3 font-bold text-cyan-400">${comp.filename}</td>`;
            tbody.appendChild(titleTr);

            comp.rows.forEach(r => {
                const tr = document.createElement('tr');
                tr.className = "border-b border-gray-800/50 hover:bg-gray-800/30";
                let rHtml = `<td class="p-3 font-medium text-gray-300">${r[0]}</td>`;
                r.slice(1).forEach(val => {
                    rHtml += `<td class="p-3 text-right text-gray-400">${val}</td>`;
                });
                tr.innerHTML = rHtml;
                tbody.appendChild(tr);
            });
        });
        
        content.classList.remove('hidden');
    } catch (err) {
        errDiv.innerText = "Connection error: " + err.message;
        errDiv.classList.remove('hidden');
    } finally {
        loading.classList.add('hidden');
    }
}

// --- Init ---
fetchDatasets();

fetch('/api/system/hardware').then(r=>r.json()).then(data => {
    if (data && data.hardware) {
        const el = document.getElementById('custom-hardware');
        if (el) el.value = data.hardware;
    }
}).catch(e => console.error(e));

fetch('/api/test/status?last_log_idx=0').then(r => r.json()).then(data => {
    if (data.is_testing) {
        lastLogLine = data.last_log_idx;
        updateUIState(true, data.progress);
        startStatusPolling();
    }
});

