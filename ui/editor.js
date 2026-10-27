'use strict';
// ---------------------------------------------------------------- the Edit tab
// A video editor: trim, split, join and reorder clips, speed parts up, transitions, brightness and crop per clip.
// An edit is a list of clips (a part of a video, its speed, sound, brightness, crop and the transition from the
// clip before it). It plays live in this page from browser-playable copies: three <video> elements take turns,
// one per stretch of video, and transitions are blended with CSS the way ffmpeg blends them. Nothing is rendered
// until Export (editor.py), which follows the same timing rules as edLayout().
// Uses the helpers of the main script in index.html ($, api, media, esc, toast, clock, state, the settings).

const ED_SPEEDS = [1, 1.5, 2, 3, 4, 6, 8, 12, 16, 20, 30];
const ED_TRANSITIONS = [['cut', 'Cut', 'straight to the next clip'], ['fade', 'Crossfade', 'one picture blends into the next'], ['black', 'Dip to black', 'fades out, then in'], ['wipe', 'Wipe', 'the next clip pushes in from the right edge'], ['slide', 'Slide', 'the next clip slides in, pushing this one out']];
const ED_OVERLAP = new Set(['fade', 'wipe', 'slide']);
const ED_DURATIONS = [0.5, 1, 2];
const ED_MAX_RATE = 16;            // browsers play a video at most 16× as fast; faster parts get a sped-up copy
const ED_SOUND_LIMIT = 2;          // speeding up past this mutes a clip (its sound can be switched back on)
const ED_PAD = 16;                 // room left of the timeline's zero, for the "fade in" button
const ED_LANE = 27;                // height of one row of texts
const edSvg = (paths) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
const ED_ICON = {
	split: edSvg('<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/>'),
	join: edSvg('<path d="M10 14a4.2 4.2 0 0 0 6 0l3-3a4.2 4.2 0 0 0-6-6l-1 1"/><path d="M14 10a4.2 4.2 0 0 0-6 0l-3 3a4.2 4.2 0 0 0 6 6l1-1"/>'),
	speed: edSvg('<path d="M4.6 17.5a8.5 8.5 0 1 1 14.8 0"/><path d="M12 13.5l4-4.5"/>'),
	trimStart: edSvg('<path d="M11 5H7v14h4"/><path d="M14 12h6"/>'),
	trimEnd: edSvg('<path d="M13 5h4v14h-4"/><path d="M4 12h6"/>'),
	copy: edSvg('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>'),
	paste: edSvg('<rect x="5" y="5" width="14" height="16" rx="2"/><path d="M9 3h6v4H9z"/>'),
	duplicate: edSvg('<rect x="3" y="8" width="12" height="12" rx="2"/><path d="M9 4h9a2 2 0 0 1 2 2v9M9 11v6M6 14h6"/>'),
	add: edSvg('<path d="M12 5v14M5 12h14"/>'),
	text: edSvg('<path d="M5 7V5h14v2M12 5v14M9 19h6"/>'),
	edit: edSvg('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>'),
	part: edSvg('<path d="M6 4v16M18 4v16"/><path d="M9 12h6"/>'),
	undo: edSvg('<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>'),
	redo: edSvg('<path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>'),
	fit: edSvg('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
	front: edSvg('<rect x="9" y="9" width="11" height="11" rx="2" fill="currentColor" fill-opacity=".25"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>'),
	clock: edSvg('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
	move: edSvg('<path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/>'),
	check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
	cut: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 4v16"/></svg>',
	fade: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M3 18L21 6M3 6l18 12"/></svg>',
	black: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="6"/></svg>',
	wipe: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><rect x="4" y="5" width="16" height="14" rx="2"/><path d="M13 5v14"/></svg>',
	slide: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h14M13 7l5 5-5 5"/></svg>',
	fadeEdge: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 18L20 6"/><path d="M4 18h16" opacity=".5"/></svg>',
	play: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
	pause: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
	sound: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/></svg>',
	muted: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9v6h4l5 4V5L8 9z"/><path d="M17 9l5 6M22 9l-5 6"/></svg>',
	trash: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>'
};
const ed = {
	project: null, media: {}, speedCopies: {}, layout: null, ready: null,
	selected: null, range: null, t: 0, playing: false, lead: 0, wall: 0, stopAt: null,
	zoom: 1, pps: 50, volume: 1, muted: false,
	history: [], future: [], saveTimer: null, sliding: false,
	drag: null, crop: null, menu: null, library: null, libraryTime: 0, jobSeen: null,
	clipboard: null, textSel: null, textEdit: null, lanes: 1, seekDrag: false
};
const edVideos = [...document.querySelectorAll('#ed-stage video')];
const edLayers = [...document.querySelectorAll('#ed-stage .ed-layer')];
const edFrames = [...document.querySelectorAll('#ed-stage .ed-frame')];
const edId = () => Math.random().toString(16).slice(2, 12).padEnd(10, '0');
const edSpeedLabel = (speed) => String(speed).replace('.5', '½') + '×';
const edClips = () => ed.project ? ed.project.clips : [];
const edTexts = () => ed.project ? (ed.project.texts ||= []) : [];
const edText = (id) => edTexts().find(t => t.id === id) || null;
const edInfo = (clip) => ed.media[clip.path]?.info || null;
const edItem = (id) => ed.layout?.clips.find(it => it.c.id === id) || null;
const edFps = () => ed.layout?.fps || 30;

// ---------------------------------------------------------------- tab switches (showTab() is in index.html)
function edTabShown(tab) {
	document.body.classList.toggle('tab-edit', tab === 'edit');
	if (tab === 'edit') edEnter();
	else { edPause(); edCloseMenu(); edCloseContext(); edEndTextEdit(); }
}
function edEnter() {
	ed.ready ||= edBoot();
	edLoadLibrary(true);
	requestAnimationFrame(() => { edFitStage(); edRenderAll(); });
}
async function edBoot() {
	try {
		const { edits } = await api('/api/edit/list', {});
		let wanted = null;
		try { wanted = localStorage.getItem('edit'); } catch {}
		const pick = edits.find(e => e.id === wanted) || edits[0];
		edSetProject(pick ? await api('/api/edit/open', { id: pick.id }) : await api('/api/edit/new', {}));
	} catch (error) { toast('The editor could not start: ' + error.message); }
}
// "Edit" on a finished video in the Upscale tab
async function edOpenVideo(path) {
	showTab('edit');
	await ed.ready;
	await edAddVideo(path);
}

// ---------------------------------------------------------------- the edit and its videos
function edSetProject(project) {
	edPause();
	ed.project = project;
	ed.history = []; ed.future = [];
	edEndTextEdit();
	ed.selected = null; ed.range = null; ed.t = 0; ed.crop = null; ed.textSel = null;
	try { localStorage.setItem('edit', project.id); } catch {}
	$('ed-name').value = project.name;
	for (const path of new Set(project.clips.map(c => c.path))) edEnsureMedia(path);
	ed.fileDirty = !!(project.file && project.updated > (project.file_saved || 0) + 1);
	edRelayout();
	edSetSaved('');
}
function edMediaState(path) { return ed.media[path] ||= { info: null, media: null, error: null, busy: false }; }
async function edProbe(path) {
	const m = edMediaState(path);
	if (!m.info) m.info = await api('/api/edit/probe', { path });
	return m.info;
}
// the browser-playable copy (old AVI/MOV files need a copy first, made once), then the timeline's picture strip
function edEnsureMedia(path) {
	const m = edMediaState(path);
	if (m.media || m.busy || m.error) return;
	m.busy = true;
	(async () => {
		try {
			if (!m.info) { await edProbe(path); edRelayout(); }
			m.media = await api('/api/edit/media', { path });
		} catch (error) { m.error = error.message; }
		m.busy = false;
		edRelayout();
		if (m.media && !m.media.strip) api('/api/edit/strip', { path }).then(result => { m.media.strip = result.strip; edRenderTimeline(); }).catch(() => {});
	})();
}
const edSpeedKey = (c) => `${c.path}|${c.in.toFixed(3)}|${c.out.toFixed(3)}|${c.speed}`;
// parts faster than browsers play get a copy that is already sped up; until then they play at 16×
function edRequestSpeedCopies() {
	for (const c of edClips()) {
		if (c.speed <= ED_MAX_RATE || !ed.media[c.path]?.media) continue;
		const key = edSpeedKey(c);
		if (ed.speedCopies[key]) continue;
		ed.speedCopies[key] = { url: null };
		api('/api/edit/speed', { path: c.path, start: c.in, end: c.out, speed: c.speed })
			.then(result => { ed.speedCopies[key] = result; edRelayout(); })
			.catch(() => { ed.speedCopies[key] = { url: null, failed: true }; });
	}
}
async function edAddVideo(path, index) {
	if (!ed.project) return;
	try {
		const info = await edProbe(path);
		const clip = { id: edId(), path: info.path, name: info.name, in: 0, out: info.duration, speed: 1, sound: true, gamma: 1, crop: null, transition: { type: 'cut', duration: 1 } };
		edChange(() => edClips().splice(index == null ? edClips().length : index, 0, clip));
		ed.selected = clip.id; ed.range = null;
		edEnsureMedia(info.path);
		edSeek(edItem(clip.id).start);
		edRenderAll();
		edScrollTo(ed.t);
	} catch (error) { toast(error.message); }
}

// ---------------------------------------------------------------- timing (the same rules as editor.py plan())
function edLayout() {
	const project = ed.project, clips = edClips(), items = [];
	clips.forEach((c, i) => items.push({ c, i, len: Math.max((c.out - c.in) / c.speed, 0.01), start: 0, end: 0, overlapIn: 0, overlapOut: 0, fadeIn: 0, fadeOut: 0, kind: 'cut' }));
	items.forEach((it, i) => {
		if (!i) { it.fadeIn = Math.min(project.fade_in || 0, it.len / 2); return; }
		const before = items[i - 1], t = it.c.transition || {}, kind = t.type || 'cut', room = Math.min(before.len, it.len) / 2;
		if (ED_OVERLAP.has(kind)) {
			const d = Math.min(t.duration || 1, room);
			if (d >= 0.07) { it.overlapIn = before.overlapOut = d; it.kind = kind; }
		} else if (kind === 'black') {
			const half = Math.min((t.duration || 1) / 2, room);
			if (half >= 0.035) { before.fadeOut = it.fadeIn = half; it.kind = kind; }
		}
	});
	if (items.length) { const last = items[items.length - 1]; last.fadeOut = Math.min(project.fade_out || 0, last.len / 2); }
	let position = 0;
	for (const it of items) { position -= it.overlapIn; it.start = position; it.end = position + it.len; position = it.end; }
	// frame: the shape most of the edit has, at the size of the largest clip of that shape; frame rate: the highest (≤ 60)
	// (shapes within 6% of each other count as one: a crop is rarely exactly 4:3)
	const shapes = [];
	let fps = 0;
	for (const it of items) {
		const info = edInfo(it.c); if (!info) continue;
		const crop = it.c.crop, w = (crop ? crop.w : info.width) * info.sar, h = crop ? crop.h : info.height;
		let shape = shapes.find(s => Math.abs(w / h / s.aspect - 1) < 0.06);
		if (!shape) shapes.push(shape = { aspect: w / h, weight: 0, sizes: [] });
		shape.weight += it.len;
		shape.sizes.push({ w, h });
		fps = Math.max(fps, info.fps);
	}
	let frame = { w: 1920, h: 1080 };
	if (shapes.length) {
		const best = shapes.reduce((a, b) => b.weight > a.weight ? b : a).sizes.reduce((a, b) => b.h > a.h ? b : a);
		frame = { w: Math.max(Math.round(best.w / 2) * 2, 2), h: Math.max(Math.round(best.h / 2) * 2, 2) };
	}
	// groups: clips that follow on in the same video play on one <video> element without a switch
	const groups = [];
	for (const it of items) {
		const before = it.i ? items[it.i - 1] : null, group = groups[groups.length - 1];
		const follows = before && group && it.c.path === before.c.path && it.kind === 'cut' && !before.fadeOut && !it.fadeIn
			&& Math.abs(it.c.in - before.c.out) < 0.02 && !edUsesCopy(before.c) && !edUsesCopy(it.c);
		if (follows) group.items.push(it); else groups.push({ index: groups.length, items: [it] });
		it.group = groups[groups.length - 1];
	}
	return { clips: items, total: position, frame, fps: Math.min(fps || 30, 60), groups };
}
function edUsesCopy(c) { return c.speed > ED_MAX_RATE && !!ed.speedCopies[edSpeedKey(c)]?.url; }
function edSource(c) {
	const m = ed.media[c.path];
	if (!m || !m.media) return null;
	if (edUsesCopy(c)) return { url: ed.speedCopies[edSpeedKey(c)].url, start: 0, scale: 1, rate: 1 };
	return { url: m.media.url, start: c.in, scale: c.speed, rate: Math.min(c.speed, ED_MAX_RATE) };
}

// ---------------------------------------------------------------- changes, undo, saving
const edSnapshot = () => JSON.stringify({ clips: ed.project.clips, texts: edTexts(), fade_in: ed.project.fade_in || 0, fade_out: ed.project.fade_out || 0 });
function edCheckpoint() {
	const snap = edSnapshot();
	if (ed.history[ed.history.length - 1] !== snap) ed.history.push(snap);
	if (ed.history.length > 300) ed.history.shift();
	ed.future = [];
}
function edChange(fn) { edCheckpoint(); fn(); edChanged(); }
function edChanged() { edRelayout(); edScheduleSave(); }
function edRestore(snap) {
	edEndTextEdit(true);
	Object.assign(ed.project, JSON.parse(snap));
	if (ed.selected && !edClips().some(c => c.id === ed.selected)) ed.selected = null;
	if (ed.textSel && !edText(ed.textSel)) ed.textSel = null;
	ed.range = null; ed.crop = null;
	for (const c of edClips()) edEnsureMedia(c.path);
	edChanged();
}
function edUndo() { if (!ed.history.length) return; ed.future.push(edSnapshot()); edRestore(ed.history.pop()); toast('Undone'); }
function edRedo() { if (!ed.future.length) return; ed.history.push(edSnapshot()); edRestore(ed.future.pop()); toast('Redone'); }
function edScheduleSave() { clearTimeout(ed.saveTimer); ed.fileDirty = true; edSetSaved('Saving…'); ed.saveTimer = setTimeout(edSaveNow, 500); }
async function edSaveNow() {
	clearTimeout(ed.saveTimer);
	if (!ed.project) return;
	try { await api('/api/edit/save', { project: ed.project }); edSetSaved('All changes saved'); }
	catch (error) { edSetSaved('Not saved: ' + error.message); }
}
// the edit itself is saved all the time; with a project file the status says whether the file is up to date too
function edSetSaved(text) {
	if (text != null) ed.savedText = text;
	const file = edFileName();
	$('ed-saved').textContent = !file ? ed.savedText || '' : ed.fileDirty ? `Not saved in ${file} yet · Ctrl+S` : `Saved in ${file}`;
	$('ed-saved').title = file ? ed.project.file : 'Edits are kept automatically; Ctrl+S also saves this one as a project file';
	// (the status line gives way on narrower windows; the dot on the Save button stays)
	$('ed-save-file').classList.toggle('dirty', !!file && ed.fileDirty);
	$('ed-save-file').title = !file ? 'Save as a project file, to open again later (Ctrl+S)' : ed.fileDirty ? `Save the changes in ${file} (Ctrl+S)` : `Saved in ${file}`;
}
function edRelayout() {
	if (!ed.project) return;
	const playing = ed.playing;
	if (playing) edStopVideos();
	ed.layout = edLayout();
	ed.t = Math.min(Math.max(ed.t, 0), ed.layout.total);
	edRequestSpeedCopies();
	edFitStage();
	edRenderAll();
	if (playing && ed.layout.clips.length) edStartAt(ed.t); else { ed.playing = false; edShowAt(ed.t); }
}

// ---------------------------------------------------------------- picture: crop, fit and brightness on the stage
function edFitStage() {
	const box = $('ed-stage-box'), stage = $('ed-stage'), tab = $('edit-tab'), frame = ed.layout ? ed.layout.frame : { w: 16, h: 9 };
	// the tab is as tall as the window below the top bar; the player box gets what the panels and timeline leave
	tab.style.setProperty('--ed-top', tab.offsetTop + 'px');
	if (!box.clientWidth || !box.clientHeight) return;
	let w = box.clientWidth, h = w * frame.h / frame.w;
	if (h > box.clientHeight) { h = box.clientHeight; w = h * frame.w / frame.h; }
	stage.style.width = w + 'px'; stage.style.height = h + 'px';
	edPlaceCrop();
	edPaintTexts(ed.t);
	if (ed.textEdit) edPlaceTextEditor();
}
// the player box changes with the window and the timeline; the top bar is lower on short windows
const edFitObserver = new ResizeObserver(() => { if (state.tab === 'edit') { edRenderTimeline(); edFitStage(); } });
edFitObserver.observe($('ed-stage-box'));
edFitObserver.observe(document.querySelector('.topbar'));
// where the clip's picture sits in the frame (in % of the frame), and which part of the video it shows
function edFrameBox(c, crop) {
	const info = edInfo(c), frame = ed.layout.frame;
	const cx = crop ? crop.x : 0, cy = crop ? crop.y : 0, cw = crop ? crop.w : info.width, ch = crop ? crop.h : info.height;
	const scale = Math.min(frame.w / (cw * info.sar), frame.h / ch), fw = cw * info.sar * scale, fh = ch * scale;
	return { left: (frame.w - fw) / 2 / frame.w * 100, top: (frame.h - fh) / 2 / frame.h * 100, width: fw / frame.w * 100, height: fh / frame.h * 100,
		video: { left: -cx / cw * 100, top: -cy / ch * 100, width: info.width / cw * 100, height: info.height / ch * 100 } };
}
function edPlaceFrame(slot, c) {
	if (!edInfo(c)) return;
	const crop = ed.crop && ed.crop.id === c.id ? null : c.crop;   // while drawing a crop the whole picture shows
	const box = edFrameBox(c, crop), f = edFrames[slot].style, v = edVideos[slot].style;
	f.left = box.left + '%'; f.top = box.top + '%'; f.width = box.width + '%'; f.height = box.height + '%';
	v.left = box.video.left + '%'; v.top = box.video.top + '%'; v.width = box.video.width + '%'; v.height = box.video.height + '%';
	v.filter = c.gamma && Math.abs(c.gamma - 1) > 0.001 ? `url(#${edGammaFilter(c.gamma, edInfo(c))})` : '';
}
// Brightness: the upscaler's ffmpeg eq=gamma works on the stored luma values (16–235); the page gets RGB, so the
// filter turns RGB into luma + colour, applies the same curve to the luma, and turns it back
function edGammaFilter(gamma, info) {
	const hd = info.height >= 720, id = `ed-gamma-${hd ? 709 : 601}-${Math.round(gamma * 100)}`;
	if (document.getElementById(id)) return id;
	const kr = hd ? 0.2126 : 0.299, kb = hd ? 0.0722 : 0.114, kg = 1 - kr - kb;
	const toYuv = [kr, kg, kb, 0, 0, -kr / (2 * (1 - kb)), -kg / (2 * (1 - kb)), 0.5, 0, 0.5, 0.5, -kg / (2 * (1 - kr)), -kb / (2 * (1 - kr)), 0, 0.5, 0, 0, 0, 1, 0];
	const toRgb = [1, 0, 2 * (1 - kr), 0, -(1 - kr), 1, -2 * kb * (1 - kb) / kg, -2 * kr * (1 - kr) / kg, 0, (kb * (1 - kb) + kr * (1 - kr)) / kg, 1, 2 * (1 - kb), 0, 0, -(1 - kb), 0, 0, 0, 1, 0];
	const table = [];
	for (let k = 0; k <= 32; k++) {
		const stored = (k / 32 * 219 + 16) / 255, out = Math.pow(stored, 1 / gamma) * 255;
		table.push(Math.min(Math.max((out - 16) / 219, 0), 1).toFixed(4));
	}
	const filter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
	filter.id = id;
	filter.setAttribute('color-interpolation-filters', 'sRGB');
	filter.innerHTML = `<feColorMatrix type="matrix" values="${toYuv.map(n => +n.toFixed(5)).join(' ')}"/><feComponentTransfer><feFuncR type="table" tableValues="${table.join(' ')}"/></feComponentTransfer><feColorMatrix type="matrix" values="${toRgb.map(n => +n.toFixed(5)).join(' ')}"/>`;
	$('ed-filters').appendChild(filter);
	return id;
}

// ---------------------------------------------------------------- playback
for (const v of edVideos) {
	v.addEventListener('seeked', () => edPendingSeek(v));
	v.addEventListener('loadedmetadata', () => edPendingSeek(v));
	v.addEventListener('error', () => { if (v.dataset.url) toast('A video could not be played: ' + v.dataset.url.split(/[\\/]/).pop()); });
}
// seeks are queued while the element is still busy, so scrubbing asks for the newest position only
function edPendingSeek(v) {
	if (v._target == null || v.seeking) return;
	const target = v._target;
	v._target = null;
	if (Math.abs(v.currentTime - target) > 0.0005) v.currentTime = target;
}
function edSeekVideo(v, time) {
	if (v.readyState < 1 || v.seeking) { v._target = time; return; }
	v._target = null;
	if (Math.abs(v.currentTime - time) > 0.0005) v.currentTime = time;
}
const edSlot = (it) => it.group.index % 3;
// the element of this clip's group, showing (or about to show) the moment T
function edPrepare(it, T) {
	const src = edSource(it.c); if (!src) return null;
	const v = edVideos[edSlot(it)];
	if (v.dataset.url !== src.url) { v.dataset.url = src.url; v._target = null; v.src = media(src.url); }
	const local = Math.min(Math.max(T - it.start, 0), Math.max(it.len - 0.002, 0));
	edSeekVideo(v, src.start + local * src.scale);
	if (v.playbackRate !== src.rate) v.defaultPlaybackRate = v.playbackRate = src.rate;
	return v;
}
function edActiveAt(T) {
	const items = ed.layout ? ed.layout.clips : [];
	if (!items.length) return [];
	if (ed.crop) return [edItem(ed.crop.id)].filter(Boolean);
	if (T >= ed.layout.total - 1e-6) return [items[items.length - 1]];
	const active = items.filter(it => T >= it.start - 1e-9 && T < it.end);
	return active.length ? active : [items[0]];
}
// opacity, wipe and slide of the clips at T, and their sound level; the same curves as ffmpeg's fade and xfade
function edPaint(T, active) {
	const used = new Set();
	for (const it of active) {
		const slot = edSlot(it), layer = edLayers[slot], v = edVideos[slot];
		used.add(slot);
		const local = Math.min(Math.max(T - it.start, 0), it.len);
		let opacity = 1, clip = '', shift = 0, gain = 1;
		if (!ed.crop) {
			if (it.fadeIn && local < it.fadeIn) opacity *= local / it.fadeIn;
			if (it.fadeOut && local > it.len - it.fadeOut) opacity *= (it.len - local) / it.fadeOut;
			gain = opacity;
			if (it.overlapIn && local < it.overlapIn) {
				const p = local / it.overlapIn;
				gain *= p;
				if (it.kind === 'fade') opacity *= p;
				else if (it.kind === 'wipe') clip = `inset(0 0 0 ${(1 - p) * 100}%)`;
				else if (it.kind === 'slide') shift = (1 - p) * 100;
			}
			if (it.overlapOut && local > it.len - it.overlapOut) {
				const p = (local - (it.len - it.overlapOut)) / it.overlapOut;
				gain *= 1 - p;
				if (ed.layout.clips[it.i + 1]?.kind === 'slide') shift = -p * 100;
			}
		}
		layer.hidden = !edSource(it.c);
		layer.style.opacity = Math.max(0, Math.min(1, opacity));
		layer.style.clipPath = clip;
		layer.style.transform = shift ? `translateX(${shift}%)` : '';
		layer.style.zIndex = 1 + it.i;
		edPlaceFrame(slot, it.c);
		v.muted = ed.muted || it.c.sound === false || !edInfo(it.c)?.has_audio;
		v.volume = Math.max(0, Math.min(1, gain * ed.volume));
	}
	edLayers.forEach((layer, slot) => { if (!used.has(slot)) layer.hidden = true; });
	edPaintTexts(T);
	// a clip whose playable copy is still being made
	const waiting = active.find(it => !edSource(it.c));
	const m = waiting && ed.media[waiting.c.path];
	$('ed-wait').hidden = !waiting;
	ed.waiting = waiting ? waiting.c.path : null;
	if (waiting) {
		const html = m && m.error ? `${esc(waiting.c.name)} cannot be played: ${esc(m.error)}` : `<div style="width:min(420px,90%)"><b><span class="spinner"></span> Preparing ${esc(waiting.c.name)} for playback <span id="ed-wait-pct"></span></b><div class="bar" id="ed-wait-bar" hidden><div></div></div><div class="small" style="margin-top:8px">Browsers cannot play this video format, so it gets a playable copy, once.</div></div>`;
		if ($('ed-wait').dataset.html !== html) { $('ed-wait').innerHTML = html; $('ed-wait').dataset.html = html; }
		edRenderWaitProgress();
	}
	$('ed-empty').hidden = !!(ed.layout && ed.layout.clips.length);
}
function edRenderWaitProgress() {
	const fraction = ed.waiting && state.data?.edit?.preparing?.[ed.waiting], bar = $('ed-wait-bar');
	if (fraction == null || !bar) return;
	$('ed-wait-pct').textContent = `· ${Math.round(fraction * 100)}%`;
	bar.hidden = false;
	bar.firstElementChild.style.width = fraction * 100 + '%';
}
function edShowAt(T) {
	if (!ed.layout) return;
	const active = edActiveAt(T);
	for (const it of active) edPrepare(it, T);
	edPaint(T, active);
	edRenderPlayhead(); edRenderTime();
}
function edStopVideos() { for (const v of edVideos) if (!v.paused) v.pause(); }
function edPlay(stopAt) {
	if (!ed.layout || !ed.layout.clips.length || ed.playing) return;
	if (ed.crop) edCropDone(true);
	if (ed.t >= ed.layout.total - 0.02) ed.t = 0;
	ed.stopAt = stopAt ?? null;
	ed.playing = true;
	edStartAt(ed.t);
	edRenderTransport();
	requestAnimationFrame(edTick);
}
function edPause() {
	if (!ed.playing) return;
	ed.playing = false; ed.stopAt = null;
	edStopVideos();
	edShowAt(ed.t);
	edRenderTransport();
}
function edToggle() { ed.playing ? edPause() : edPlay(); }
function edStartAt(T) {
	const active = edActiveAt(T);
	if (!active.length) return;
	ed.lead = active[0].i;
	ed.wall = performance.now();
	for (const it of active) { const v = edPrepare(it, T); if (v) v.play().catch(() => {}); }
	edPrefetch(active[active.length - 1].group.index);
	edPaint(T, active);
}
// the next two stretches are loaded and waiting at their first frame, so a cut does not stall
function edPrefetch(groupIndex) {
	for (const g of [groupIndex + 1, groupIndex + 2]) {
		const group = ed.layout.groups[g]; if (!group) continue;
		if (edVideos[g % 3].paused) edPrepare(group.items[0], group.items[0].start);
	}
}
// one step of playback: the leading clip's video is the clock
function edTick() {
	if (!ed.playing) return;
	const items = ed.layout.clips, now = performance.now(), dt = (now - ed.wall) / 1000;
	ed.wall = now;
	let lead = items[ed.lead];
	if (!lead) { edPause(); return; }
	const src = edSource(lead.c), v = edVideos[edSlot(lead)];
	let T;
	if (!src) T = ed.t + dt;          // not playable yet: time runs on by the clock
	else if (v.seeking || v._target != null || v.dataset.url !== src.url) T = ed.t;
	else T = lead.start + (v.currentTime - src.start) / src.scale;
	T = Math.max(T, lead.start);
	if (T >= lead.end - 0.004 || (src && v.ended)) {
		const next = items[lead.i + 1];
		if (!next) { ed.t = ed.layout.total; edPause(); return; }
		if (next.group === lead.group) {
			const ns = edSource(next.c);
			if (ns && v.playbackRate !== ns.rate) v.defaultPlaybackRate = v.playbackRate = ns.rate;
		} else {
			const nv = edVideos[edSlot(next)];
			if (nv.paused) { edPrepare(next, Math.max(T, next.start)); nv.play().catch(() => {}); }
			v.pause();
			edPrefetch(next.group.index);
		}
		ed.lead = next.i; lead = next;
		T = Math.max(T, next.start);
	}
	// a transition: the next clip starts while this one still plays
	const next = items[lead.i + 1];
	if (next && next.group !== lead.group && T >= next.start) {
		const nv = edVideos[edSlot(next)];
		if (nv.paused) { edPrepare(next, T); nv.play().catch(() => {}); }
	}
	ed.t = Math.min(T, ed.layout.total);
	if (ed.stopAt != null && ed.t >= ed.stopAt) { ed.t = Math.min(ed.stopAt, ed.layout.total); edPause(); return; }
	edPaint(ed.t, edActiveAt(ed.t));
	edRenderPlayhead(true); edRenderTime();
	requestAnimationFrame(edTick);
}
function edSeek(T) {
	if (!ed.layout) return;
	ed.t = Math.min(Math.max(T, 0), ed.layout.total);
	if (ed.playing) { edStopVideos(); edStartAt(ed.t); }
	else edShowAt(ed.t);
	edRenderPlayhead(); edRenderTime(); edRenderPlayheadButtons();
}
function edPlayFrom(T, until) { edPause(); edSeek(Math.max(T, 0)); edPlay(until); }

// ---------------------------------------------------------------- editing operations
// the clip that can be split at timeline time T: none at its edges or inside a transition
const edSplitTarget = (T) => ed.layout?.clips.find(it => T > it.start + it.overlapIn + 0.02 && T < it.end - it.overlapOut - 0.02) || null;
// splits the clip at timeline time T into two; null when T is at an edge or inside a transition
function edSplitAt(T) {
	const it = edSplitTarget(T);
	if (!it) return null;
	const c = it.c, at = c.in + (T - it.start) * c.speed;
	const second = { ...c, id: edId(), in: at, crop: c.crop ? { ...c.crop } : null, transition: { type: 'cut', duration: c.transition?.duration || 1 } };
	c.out = at;
	edClips().splice(it.i + 1, 0, second);
	ed.layout = edLayout();
	return { first: c, second };
}
function edSplit() {
	if (!ed.layout || !ed.layout.clips.length) return;
	edPause();
	if (!edSplitTarget(ed.t)) {
		toast(ed.layout.clips.some(it => ed.t > it.start && ed.t < it.start + it.overlapIn) ? 'The playhead is inside a transition: move it a little to split there' : 'Move the playhead into a clip to split it there');
		return;
	}
	edCheckpoint();
	const parts = edSplitAt(ed.t);
	// the part before the playhead stays selected: split again at the end of a part, and the part is selected
	ed.selected = parts.first.id; ed.range = null; ed.textSel = null;
	edChanged();
}
// two clips that continue each other in one video (as after a split) become one clip again, with the settings of keep
const edCanJoin = (i) => { const a = edClips()[i], b = edClips()[i + 1]; return !!(a && b && a.path === b.path && Math.abs(b.in - a.out) < 0.02); };
function edJoin(i, keep) {
	const a = edClips()[i], b = edClips()[i + 1];
	if (!edCanJoin(i)) return;
	edChange(() => {
		const transition = a.transition;
		keep.in = a.in; keep.out = b.out; keep.transition = transition;
		edClips().splice(keep === a ? i + 1 : i, 1);
	});
	ed.selected = keep.id; ed.range = null; ed.textSel = null;
	edRenderAll();
	toast('Joined into one clip');
}
function edSetClipSpeed(id, speed) {
	const c = edClips().find(c => c.id === id); if (!c) return;
	edChange(() => edSetSpeed([c], speed));
	const item = edItem(id);
	edPlayFrom(item.start, Math.min(item.end, item.start + 6));
}
// "Start here" / "End here": the clip starts or ends at the playhead
function edTrimTo(id, side) {
	const it = edItem(id); if (!it || ed.t <= it.start + 0.02 || ed.t >= it.end - 0.02) return;
	const c = it.c, at = c.in + (ed.t - it.start) * c.speed;
	edChange(() => { if (side === 'start') c.in = at; else c.out = at; });
	edSeek(side === 'start' ? edItem(id).start : Math.max(edItem(id).end - 1 / edFps(), 0));
}
function edDuplicate(id) {
	const it = edItem(id); if (!it) return;
	const copy = { ...JSON.parse(JSON.stringify(it.c)), id: edId(), transition: { type: 'cut', duration: 1 } };
	edChange(() => edClips().splice(it.i + 1, 0, copy));
	ed.selected = copy.id; ed.textSel = null;
	edRenderAll();
}
// copy and paste of a clip or a text (also into another edit)
function edCopy(id, quiet) {
	const c = edClips().find(c => c.id === id), t = edText(id);
	if (!c && !t) return false;
	ed.clipboard = { kind: c ? 'clip' : 'text', data: JSON.parse(JSON.stringify(c || t)) };
	if (!quiet) toast(`${c ? 'Clip' : 'Text'} copied: Ctrl+V pastes it ${c ? 'after the selected clip' : 'at the playhead'}`);
	return true;
}
function edCut(id) {
	if (!edCopy(id, true)) return;
	if (edText(id)) edDeleteText(id);
	else { ed.selected = id; ed.range = null; ed.textSel = null; edDelete(); }
	toast('Cut: Ctrl+V pastes it');
}
function edPaste(index) {
	const board = ed.clipboard; if (!board || !ed.project) return;
	const copy = { ...JSON.parse(JSON.stringify(board.data)), id: edId() };
	if (board.kind === 'text') {
		const length = copy.end - copy.start;
		copy.start = Math.min(ed.t, Math.max((ed.layout?.total || 0) - 0.5, 0)); copy.end = copy.start + length;
		edChange(() => edTexts().push(copy));
		edSelectText(copy.id);
		return;
	}
	copy.transition = { type: 'cut', duration: 1 };
	if (index == null) { const it = edItem(ed.selected); index = it ? it.i + 1 : edInsertIndex(ed.t); }
	edChange(() => edClips().splice(index, 0, copy));
	ed.selected = copy.id; ed.range = null; ed.textSel = null;
	edEnsureMedia(copy.path);
	edSeek(edItem(copy.id).start);
	edRenderAll();
}
// the clips between timeline times a and b, split off from the rest; an end inside a transition moves to its middle
function edPartClips(a, b) {
	const snap = (T) => { const it = ed.layout.clips.find(it => it.overlapIn && T > it.start && T < it.start + it.overlapIn); return it ? it.start + it.overlapIn / 2 : T; };
	a = snap(a); b = snap(b);
	edSplitAt(a); edSplitAt(b);
	return ed.layout.clips.filter(it => it.start + it.overlapIn / 2 >= a - 0.03 && it.end - it.overlapOut / 2 <= b + 0.03).map(it => it.c);
}
function edSetSpeed(clips, speed) {
	for (const c of clips) {
		// crossing 2×: sound off when faster, back on when slower
		if ((c.speed <= ED_SOUND_LIMIT) !== (speed <= ED_SOUND_LIMIT)) c.sound = speed <= ED_SOUND_LIMIT;
		c.speed = speed;
	}
}
function edRangeSpeed(speed) {
	const { a, b } = ed.range;
	edCheckpoint();
	const clips = edPartClips(a, b);
	if (!clips.length) { ed.history.pop(); return; }
	edSetSpeed(clips, speed);
	ed.layout = edLayout();
	const items = clips.map(c => edItem(c.id));
	ed.range = { a: items[0].start, b: items[items.length - 1].end };
	edChanged();
	edPlayFrom(ed.range.a - 0.5, Math.min(ed.range.b, ed.range.a + 6));
}
function edRangeDelete(keep) {
	const { a, b } = ed.range;
	edCheckpoint();
	const inside = new Set(edPartClips(a, b).map(c => c.id));
	if (!inside.size) { ed.history.pop(); return; }
	ed.project.clips = edClips().filter(c => keep ? inside.has(c.id) : !inside.has(c.id));
	if (keep) { ed.project.clips[0].transition = { ...ed.project.clips[0].transition, type: 'cut' }; }
	ed.range = null;
	if (!edClips().some(c => c.id === ed.selected)) ed.selected = null;
	ed.t = keep ? 0 : a;
	edChanged();
	toast(keep ? 'Kept only the selected part' : 'Part cut out');
}
// the selected part becomes a clip of its own (or several, when it spans clips)
function edRangeSplit() {
	const { a, b } = ed.range;
	edCheckpoint();
	const clips = edPartClips(a, b);
	if (edSnapshot() === ed.history[ed.history.length - 1]) ed.history.pop();
	if (!clips.length) return;
	ed.range = null; ed.selected = clips[0].id; ed.textSel = null;
	edChanged();
}
function edDelete() {
	if (ed.textSel) { edDeleteText(ed.textSel); return; }
	if (ed.range) { edRangeDelete(false); return; }
	const it = edItem(ed.selected);
	if (!it) { toast('Select a clip or a part first'); return; }
	edChange(() => edClips().splice(it.i, 1));
	const next = ed.layout.clips[Math.min(it.i, ed.layout.clips.length - 1)];
	ed.selected = next ? next.c.id : null;
	edSeek(next ? next.start : 0);
	edRenderAll();
}
function edSetTransition(index, type, duration) {
	edChange(() => {
		if (index === 'start') ed.project.fade_in = type === 'cut' ? 0 : duration;
		else if (index === 'end') ed.project.fade_out = type === 'cut' ? 0 : duration;
		else edClips()[index].transition = { type, duration };
	});
	// show the result right away
	if (index === 'start') edPlayFrom(0, Math.min(ed.layout.total, duration + 2));
	else if (index === 'end') edPlayFrom(ed.layout.total - duration - 2, ed.layout.total);
	else if (type !== 'cut') { const it = ed.layout.clips[index]; edPlayFrom(it.start - 1.5, Math.min(ed.layout.total, it.start + it.overlapIn + it.fadeIn + 1.5)); }
}
function edMark(which) {
	if (!ed.layout?.clips.length) return;
	const t = ed.t, r = ed.range || { a: t, b: t };
	ed.range = which === 'a' ? { a: t, b: Math.max(r.b, t + 0.1) } : { a: Math.min(r.a, t - 0.1), b: t };
	ed.range.a = Math.max(ed.range.a, 0); ed.range.b = Math.min(ed.range.b, ed.layout.total);
	edRenderRange(); edRenderInspector();
}
function edEscape() {
	if (edCtx.panels.length) edCloseContext();
	else if (ed.menu) edCloseMenu();
	else if (ed.crop) edCropDone(false);
	else if (ed.range) { ed.range = null; edRenderRange(); edRenderInspector(); }
	else if (ed.textSel) { ed.textSel = null; edRenderTimeline(); edRenderInspector(); edPaintTexts(ed.t); }
	else if (ed.selected) { ed.selected = null; edRenderTimeline(); edRenderInspector(); }
}
function edSelect(id) {
	if (ed.crop && ed.crop.id !== id) edCropDone(true);
	edEndTextEdit();
	ed.selected = id; ed.range = null; ed.textSel = null;
	edRenderTimeline(); edRenderRange(); edRenderInspector(); edPaintTexts(ed.t);
}

// ---------------------------------------------------------------- rendering
function edRenderAll() { edRenderTimeline(); edRenderRange(); edRenderInspector(); edRenderTransport(); edRenderBar(); }
function edRenderBar() {
	const L = ed.layout;
	$('ed-summary').textContent = L && L.clips.length ? `${L.clips.length} clip${L.clips.length > 1 ? 's' : ''} · ${clock(L.total, 1)}` : '';
	$('ed-export').disabled = !L || !L.clips.length;
	$('ed-undo').disabled = !ed.history.length; $('ed-redo').disabled = !ed.future.length;
}
function edRenderTransport() {
	$('ed-play').innerHTML = ed.playing ? ED_ICON.pause : ED_ICON.play;
	$('ed-play').title = ed.playing ? 'Pause (Space)' : 'Play (Space)';
	$('ed-mute').innerHTML = ed.muted ? ED_ICON.muted : ED_ICON.sound;
	edWakeControls();
	const L = ed.layout;
	$('ed-format-info').textContent = L && L.clips.length ? `${L.frame.w}×${L.frame.h} · ${+L.fps.toFixed(2)} fps` : '';
	edRenderTime();
}
function edRenderTime() { $('ed-time').innerHTML = `${clock(ed.t, 1)} <span>/ ${clock(ed.layout ? ed.layout.total : 0, 1)}</span>`; edRenderSeek(); }
const edX = (T) => ED_PAD + T * ed.pps;
// where the row of texts starts on the track: below the clips (which are lower on short windows)
const edTextTop = () => 36 + (parseFloat(getComputedStyle(document.querySelector('.ed-timeline')).getPropertyValue('--tl-clip-h')) || 68) + 6;
function edRenderTimeline() {
	const L = ed.layout, scroll = $('ed-scroll'), inner = $('ed-inner');
	if (!L || !scroll.clientWidth) return;
	const view = Math.max(scroll.clientWidth - ED_PAD * 2 - 4, 120), total = Math.max(L.total, 1);
	ed.pps = view / total * ed.zoom;
	inner.style.width = Math.max(total * ed.pps + ED_PAD * 2, scroll.clientWidth) + 'px';
	$('ed-zoom').value = Math.log(ed.zoom) / Math.log(edMaxZoom()) * 100 || 0;
	// ruler
	const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 3600];
	const step = steps.find(s => s * ed.pps >= 70) || 7200;
	let ruler = '';
	for (let t = 0; t <= L.total + 1e-6 && t / step < 2000; t += step) ruler += `<span class="tick" style="left:${edX(t)}px">${clock(t, step < 1 ? 1 : 0)}</span>`;
	if (L.clips.length && !ed.range) ruler += `<span class="empty-hint" style="left:${Math.min(edX(L.total / 2), scroll.scrollLeft + scroll.clientWidth / 2)}px">drag here to select a part</span>`;
	$('ed-ruler').innerHTML = ruler;
	// clips, overlaps and transition buttons
	let html = '';
	for (const it of L.clips) {
		const left = edX(it.start + it.overlapIn / 2), right = edX(it.end - it.overlapOut / 2), m = ed.media[it.c.path];
		const badges = (it.c.speed !== 1 ? `<span class="tl-speed">${edSpeedLabel(it.c.speed)}</span>` : '')
			+ (it.c.sound === false && edInfo(it.c)?.has_audio ? '<span title="Sound off">🔇</span>' : '')
			+ (it.c.gamma && it.c.gamma !== 1 ? `<span title="Brightness ${it.c.gamma}">☀</span>` : '') + (it.c.crop ? '<span title="Cropped">⛶</span>' : '');
		html += `<div class="tl-clip${it.c.id === ed.selected ? ' selected' : ''}${m && m.error ? ' missing' : ''}" data-id="${it.c.id}" style="left:${left}px;width:${Math.max(right - left, 3)}px" title="${esc(it.c.name)} · ${clock(it.c.in, 1)}–${clock(it.c.out, 1)}${m && m.error ? ' · ' + esc(m.error) : ''}">
			<canvas class="tl-strip"></canvas><div class="tl-label"><span class="tl-name">${esc(it.c.name)}</span>${badges}</div>
			<span class="tl-handle l" title="Drag to trim the start"></span><span class="tl-handle r" title="Drag to trim the end"></span></div>`;
		if (it.overlapIn) html += `<div class="tl-overlap" style="left:${edX(it.start)}px;width:${it.overlapIn * ed.pps}px"></div>`;
	}
	for (const it of L.clips) {
		if (!it.i) continue;
		const kind = it.c.transition?.type || 'cut', name = ED_TRANSITIONS.find(t => t[0] === kind)[1];
		html += `<button class="tl-join${kind !== 'cut' ? ' set' : ''}" data-join="${it.i}" style="left:${edX(it.start + it.overlapIn / 2)}px" title="Transition: ${name} — click to change">${ED_ICON[kind]}</button>`;
	}
	if (L.clips.length) {
		html += `<button class="tl-join edge${ed.project.fade_in ? ' set' : ''}" data-join="start" style="left:${edX(0)}px" title="Fade in from black at the start — click to change">${ED_ICON.fadeEdge}</button>`;
		html += `<button class="tl-join edge${ed.project.fade_out ? ' set' : ''}" data-join="end" style="left:${edX(L.total)}px" title="Fade out to black at the end — click to change"><span style="transform:scaleX(-1);display:grid">${ED_ICON.fadeEdge}</span></button>`;
	} else html = '<div class="lib-empty" style="position:absolute;left:16px;top:22px">The timeline is empty: add videos from the list on the left (click <b>+</b> or drag one here), or drop video files on this page.</div>';
	// texts: a row below the clips, more rows where texts overlap in time
	const lanes = edTextLanes(), textTop = edTextTop();
	for (const t of edTexts()) {
		const left = edX(t.start), width = Math.max((Math.min(t.end, Math.max(L.total, t.start + 0.1)) - t.start) * ed.pps, 6);
		html += `<div class="tl-text${t.id === ed.textSel ? ' selected' : ''}" data-text="${t.id}" style="left:${left}px;width:${width}px;top:${textTop + lanes.of[t.id] * ED_LANE}px" title="${esc(t.text)} · ${clock(t.start, 1)}–${clock(t.end, 1)} · double-click to edit, right-click for more">
			<span class="tl-text-label">${ED_ICON.text}<span>${esc(t.text.split('\n')[0] || ' ')}</span></span><span class="tl-handle l" title="Drag to change when it appears"></span><span class="tl-handle r" title="Drag to change when it disappears"></span></div>`;
	}
	if (L.clips.length && !edTexts().length) html += `<div class="tl-text-empty" style="top:${textTop}px;left:${Math.max(scroll.scrollLeft, 0) + ED_PAD}px">Texts show here · <b>Add text</b> above, or press <kbd>T</kbd></div>`;
	$('ed-track').innerHTML = html;
	inner.style.height = 27 + textTop + lanes.count * ED_LANE + 6 + 'px';
	if (lanes.count !== ed.lanes) { ed.lanes = lanes.count; requestAnimationFrame(edFitStage); }
	for (const canvas of $('ed-track').querySelectorAll('.tl-strip')) edDrawStrip(canvas, edItem(canvas.parentElement.dataset.id));
	edRenderPlayhead(); edRenderRange(); edRenderBar();
}
const edImages = {};
function edDrawStrip(canvas, it) {
	const m = it && ed.media[it.c.path], info = m && m.info;
	if (!m || !m.media || !m.media.strip || !info) return;
	let image = edImages[m.media.strip];
	if (!image) { image = edImages[m.media.strip] = new Image(); image.onload = () => edRenderTimeline(); image.src = media(m.media.strip); }
	if (!image.complete || !image.naturalWidth) return;
	const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
	if (!w || !h) return;
	canvas.width = Math.min(Math.round(w * ratio), 16000); canvas.height = Math.round(h * ratio);
	const context = canvas.getContext('2d'), tiles = 30, tileW = image.naturalWidth / tiles, tileH = image.naturalHeight;
	const drawW = Math.max(canvas.height * tileW / tileH, 8);
	for (let x = 0; x < canvas.width; x += drawW) {
		const s = it.c.in + Math.min((x + drawW / 2) / canvas.width, 1) * (it.c.out - it.c.in);
		const index = Math.min(tiles - 1, Math.max(0, Math.floor(s / info.duration * tiles)));
		context.drawImage(image, index * tileW, 0, tileW, tileH, x, 0, drawW, canvas.height);
	}
}
function edRenderPlayhead(follow) {
	if (!ed.layout) return;
	const x = edX(ed.t);
	$('ed-playhead').style.left = x + 'px';
	if (follow) {
		const scroll = $('ed-scroll');
		if (x > scroll.scrollLeft + scroll.clientWidth - 40 || x < scroll.scrollLeft) scroll.scrollLeft = x - 60;
	}
}
function edScrollTo(T) { const scroll = $('ed-scroll'), x = edX(T); if (x < scroll.scrollLeft || x > scroll.scrollLeft + scroll.clientWidth - 40) scroll.scrollLeft = x - scroll.clientWidth / 3; }
function edRenderRange() {
	const r = ed.range, el = $('ed-range');
	el.hidden = !r;
	if (r) { el.style.left = edX(r.a) + 'px'; el.style.width = Math.max((r.b - r.a) * ed.pps, 2) + 'px'; }
	$('ed-hint').textContent = r ? 'Pick a speed for the selected part on the right, press Delete to cut it out, or right-click it for more' : ed.layout?.clips.length ? 'Click a clip to change it · right-click for all actions · drag over the time ruler to select a part' : '';
}
function edMaxZoom() { const L = ed.layout, view = Math.max($('ed-scroll').clientWidth - 40, 120); return Math.max(1, Math.max(L ? L.total : 1, 1) * 300 / view); }
function edZoomTo(zoom, anchorT, anchorX) {
	const scroll = $('ed-scroll');
	anchorT ??= ed.t;
	anchorX ??= edX(anchorT) - scroll.scrollLeft;
	ed.zoom = Math.min(Math.max(zoom, 1), edMaxZoom());
	edRenderTimeline();
	scroll.scrollLeft = edX(anchorT) - anchorX;
}
const edZoomBy = (factor) => edZoomTo(ed.zoom * factor);

// the panel on the right: the selected clip, the selected part, or how it works
function edRenderInspector() {
	const box = $('ed-inspector'), L = ed.layout;
	if (!L) { box.innerHTML = ''; return; }
	if (!L.clips.length) {
		box.innerHTML = `<div class="insp-tips"><p class="eyebrow">How it works</p>
			<div><b>1 · Add videos</b>Click a video in the middle, or <b>+</b> next to a video on the left, or drag it onto the timeline, or drop video files on this page.</div>
			<div><b>2 · Cut and arrange</b>Drag a clip's edges to trim it, drag a clip to move it, press <kbd>S</kbd> to split at the playhead. Right-click a clip for everything you can do with it.</div>
			<div><b>3 · Speed up, transitions, texts</b>Select a clip (or drag over the time ruler to select a part) and pick a speed here. Click the round buttons between clips for transitions; <b>Add text</b> puts a title on the video.</div>
			<div><b>4 · Export</b>Everything plays right away while you edit; <b>Export video…</b> makes the file.</div></div>`;
		return;
	}
	if (ed.range) { box.innerHTML = edInspectorRange(); return; }
	const text = edText(ed.textSel);
	if (text) { if (!box.contains(document.activeElement) || box.dataset.text !== text.id) box.innerHTML = edInspectorText(text); box.dataset.text = text.id; edSyncTextInspector(text); return; }
	box.dataset.text = '';
	const it = edItem(ed.selected);
	if (!it) {
		box.innerHTML = `<div class="insp-tips"><p class="eyebrow">Nothing selected</p>
			<div><b>Right-click</b>Clips, the round transition buttons, a selected part, texts and the time ruler each have a menu with everything you can do there.</div>
			<div><b>Change a clip</b>Click it on the timeline: speed, sound, transition, brightness and crop appear here.</div>
			<div><b>Speed up a part</b>Drag over the time ruler (above the clips) to select it, then pick a speed.</div>
			<div><b>Cut something out</b>Select the part the same way and press <kbd>Delete</kbd>, or split with <kbd>S</kbd> and delete a clip.</div>
			<div><b>Texts</b><b style="display:inline">Add text</b> (or <kbd>T</kbd>) puts a text on the video at the playhead. Type, then drag it where you want it.</div></div>`;
		return;
	}
	box.innerHTML = edInspectorClip(it);
	edRenderPlayheadButtons();
}
function edSpeedButtons(current, act) {
	return `<div class="speed-grid">${ED_SPEEDS.map(s => `<button data-act="${act}" data-v="${s}" class="${s === current ? 'active' : ''}${s >= 10 ? ' fast' : ''}" title="${s === 1 ? 'Normal speed' : s + ' times as fast'}">${edSpeedLabel(s)}</button>`).join('')}</div>`;
}
function edTransitionChoice(index, kind, duration, edge) {
	const options = edge ? [['cut', 'None'], ['black', edge === 'start' ? 'Fade in from black' : 'Fade out to black']] : ED_TRANSITIONS;
	return `<div class="choice">${options.map(([k, name, about]) => `<button data-act="transition" data-index="${index}" data-v="${k}" class="${k === kind ? 'active' : ''}" title="${about || ''}">${name}</button>`).join('')}</div>
		<div class="row" style="margin-top:8px"><span class="small muted">Length</span><div class="choice sm">${ED_DURATIONS.map(d => `<button data-act="transition-length" data-index="${index}" data-v="${d}" class="${kind !== 'cut' && d === duration ? 'active' : ''}" ${kind === 'cut' ? 'disabled' : ''}>${String(d).replace('0.5', '½')} s</button>`).join('')}</div></div>`;
}
function edInspectorClip(it) {
	const c = it.c, info = edInfo(c), n = ed.layout.clips.length, m = ed.media[c.path];
	const kind = c.transition?.type || 'cut', duration = c.transition?.duration || 1;
	const gamma = c.gamma || 1, hasSound = !info || info.has_audio;
	const used = c.out - c.in;
	const soundNote = !hasSound ? 'This video has no sound' : c.speed > ED_SOUND_LIMIT && c.sound ? `Sped-up sound (${edSpeedLabel(c.speed)}), pitch kept` : c.speed > ED_SOUND_LIMIT ? `Off automatically above ${ED_SOUND_LIMIT}×; switch on to keep it` : c.sound ? 'Plays with this clip' : 'This clip is silent';
	const cropText = ed.crop ? 'drag on the picture, then Done' : c.crop ? `${c.crop.w}×${c.crop.h} of ${info ? info.width + '×' + info.height : ''}` : 'whole picture';
	const overlapText = it.kind === 'cut' && kind !== 'cut' ? '<div class="insp-note">Too short for this transition: the clips need to be longer.</div>' : '';
	return `<div class="insp-head"><div style="min-width:0"><p class="eyebrow">Clip ${it.i + 1} of ${n}</p><h3 title="${esc(c.path)}">${esc(c.name)}</h3>
			<div class="small muted num">${clock(c.in, 1)} – ${clock(c.out, 1)} of the video${info ? ` (${clock(info.duration)})` : ''}${m && m.error ? `<br><span style="color:var(--bad)">${esc(m.error)}</span>` : ''}</div></div>
			<button class="icon-btn danger" data-act="delete" title="Remove this clip (Delete)">${ED_ICON.trash}</button></div>
		<div class="insp-sec"><div class="insp-label">Speed <span class="num">${clock(used, 1)} → ${clock(it.len, 1)}</span></div>${edSpeedButtons(c.speed, 'speed')}
			${c.speed > ED_MAX_RATE && !edUsesCopy(c) ? '<div class="insp-note"><span class="spinner"></span> Making a preview at this speed (plays at 16× until it is ready)</div>' : ''}</div>
		<div class="insp-sec"><label class="check"><span class="switch"><input type="checkbox" data-act="sound" ${c.sound && hasSound ? 'checked' : ''} ${hasSound ? '' : 'disabled'}><span></span></span><span><b>Sound</b><small>${soundNote}</small></span></label></div>
		<div class="insp-sec" id="insp-transition"><div class="insp-label">${it.i ? 'Transition from the clip before' : 'Start of the video'}</div>
			${it.i ? edTransitionChoice(it.i, kind, duration) : edTransitionChoice('start', ed.project.fade_in ? 'black' : 'cut', ed.project.fade_in || 1, 'start')}${overlapText}</div>
		${it.i === n - 1 ? `<div class="insp-sec"><div class="insp-label">End of the video</div>${edTransitionChoice('end', ed.project.fade_out ? 'black' : 'cut', ed.project.fade_out || 1, 'end')}</div>` : ''}
		<div class="insp-sec"><div class="insp-label">Brightness <span id="insp-gamma-label">${gamma === 1 ? 'unchanged' : gamma > 1 ? `brighter (${gamma.toFixed(2)})` : `darker (${gamma.toFixed(2)})`}</span></div>
			<div class="row" style="flex-wrap:nowrap"><input type="range" data-act="gamma" min="0.7" max="1.8" step="0.05" value="${gamma}" aria-label="Brightness"><button class="btn sm quiet" data-act="gamma-reset" ${gamma === 1 ? 'disabled' : ''}>Reset</button></div>
			<div class="insp-note">The same brightening as the upscaler's. Leave it on “unchanged” for upscaled videos that were brightened already.</div></div>
		<div class="insp-sec"><div class="insp-label">Crop <span>${cropText}</span></div>
			<div class="row"><button class="btn sm${ed.crop ? ' on' : ''}" data-act="crop" ${info ? '' : 'disabled'}>${ed.crop ? 'Done' : 'Draw on video'}</button>${ed.crop ? '<button class="btn sm quiet" data-act="crop-cancel">Cancel</button>' : ''}${c.crop && !ed.crop ? '<button class="btn sm quiet" data-act="crop-reset">Remove crop</button>' : ''}</div></div>
		<div class="insp-sec"><div class="insp-label">Trim</div>
			<div class="row"><button class="btn sm" data-act="trim-start" title="The clip starts at the playhead">Start here</button><button class="btn sm" data-act="trim-end" title="The clip ends at the playhead">End here</button><button class="btn sm" data-act="split" title="Split at the playhead (S)">Split here</button><button class="btn sm quiet" data-act="duplicate" title="Put a copy right after it">Duplicate</button></div>
			<div class="insp-note">Or drag the clip's edges on the timeline.</div></div>`;
}
function edInspectorRange() {
	const { a, b } = ed.range;
	const inside = ed.layout.clips.filter(it => it.end > a + 0.03 && it.start < b - 0.03);
	const speeds = new Set(inside.map(it => it.c.speed));
	return `<div class="insp-head"><div><p class="eyebrow">Selected part</p><h3 class="num">${clock(a, 1)} – ${clock(b, 1)}</h3>
			<div class="small muted num">${clock(b - a, 1)} · in ${inside.length} clip${inside.length === 1 ? '' : 's'}</div></div>
			<button class="icon-btn" data-act="range-clear" title="Clear the selection (Esc)">✕</button></div>
		<div class="insp-sec"><div class="insp-label">Speed of this part</div>${edSpeedButtons(speeds.size === 1 ? [...speeds][0] : null, 'range-speed')}
			<div class="insp-note">The part becomes a clip of its own; the rest keeps its speed.</div></div>
		<div class="insp-sec"><div class="row"><button class="btn sm danger" data-act="range-delete" title="Delete">Cut this part out</button><button class="btn sm" data-act="range-keep">Keep only this part</button><button class="btn sm quiet" data-act="range-play">${ED_ICON.play} Play it</button></div></div>`;
}
// "Start here" / "End here" only make sense with the playhead inside the clip
function edRenderPlayheadButtons() {
	const it = edItem(ed.selected);
	const inside = it && ed.t > it.start + 0.02 && ed.t < it.end - 0.02;
	for (const act of ['trim-start', 'trim-end', 'split']) { const b = $('ed-inspector').querySelector(`[data-act="${act}"]`); if (b) b.disabled = !inside; }
}

// ---------------------------------------------------------------- inspector actions
$('ed-inspector').addEventListener('click', (e) => {
	const b = e.target.closest('[data-act]');
	if (!b || b.disabled || b.type === 'checkbox' || b.type === 'range') return;
	const act = b.dataset.act, it = edItem(ed.selected), c = it && it.c;
	if (act === 'speed' && c) edSetClipSpeed(c.id, +b.dataset.v);
	else if (act === 'range-speed') edRangeSpeed(+b.dataset.v);
	else if (act === 'range-delete') edRangeDelete(false);
	else if (act === 'range-keep') edRangeDelete(true);
	else if (act === 'range-play') edPlayFrom(ed.range.a, ed.range.b);
	else if (act === 'range-clear') { ed.range = null; edRenderRange(); edRenderInspector(); }
	else if (act === 'transition') {
		const index = b.dataset.index === 'start' || b.dataset.index === 'end' ? b.dataset.index : +b.dataset.index;
		const current = index === 'start' ? ed.project.fade_in : index === 'end' ? ed.project.fade_out : edClips()[index].transition?.duration;
		edSetTransition(index, b.dataset.v, current || 1);
	}
	else if (act === 'transition-length') {
		const index = b.dataset.index === 'start' || b.dataset.index === 'end' ? b.dataset.index : +b.dataset.index;
		const kind = index === 'start' || index === 'end' ? 'black' : edClips()[index].transition.type;
		edSetTransition(index, kind, +b.dataset.v);
	}
	else if (act === 'delete') edDelete();
	else if (act === 'gamma-reset' && c) { edChange(() => { c.gamma = 1; }); }
	else if (act === 'crop' && c) ed.crop ? edCropDone(true) : edCropStart(it);
	else if (act === 'crop-cancel') edCropDone(false);
	else if (act === 'crop-reset' && c) edChange(() => { c.crop = null; });
	else if (act === 'trim-start' && c) edTrimTo(c.id, 'start');
	else if (act === 'trim-end' && c) edTrimTo(c.id, 'end');
	else if (act === 'split') edSplit();
	else if (act === 'duplicate' && c) edDuplicate(c.id);
});
$('ed-inspector').addEventListener('change', (e) => {
	const act = e.target.dataset.act, c = edItem(ed.selected)?.c;
	if (!c) return;
	if (act === 'sound') edChange(() => { c.sound = e.target.checked; });
	else if (act === 'gamma') { ed.sliding = false; edRenderTimeline(); edScheduleSave(); edRenderInspector(); }
});
// brightness: the picture follows the slider at once; one undo step per drag
$('ed-inspector').addEventListener('input', (e) => {
	if (e.target.dataset.act !== 'gamma') return;
	const c = edItem(ed.selected)?.c; if (!c) return;
	if (!ed.sliding) { edCheckpoint(); ed.sliding = true; }
	c.gamma = Math.round(+e.target.value * 100) / 100;
	const g = c.gamma;
	$('insp-gamma-label').textContent = g === 1 ? 'unchanged' : g > 1 ? `brighter (${g.toFixed(2)})` : `darker (${g.toFixed(2)})`;
	const it = edItem(c.id);
	if (!ed.playing && (ed.t < it.start || ed.t >= it.end)) edSeek(it.start + Math.min(it.len / 2, 1));
	else edShowAt(ed.t);
});

// ---------------------------------------------------------------- timeline mouse handling
function edTimeAt(event) { const rect = $('ed-inner').getBoundingClientRect(); return Math.min(Math.max((event.clientX - rect.left - ED_PAD) / ed.pps, 0), ed.layout ? ed.layout.total : 0); }
function edInsertIndex(T, without) {
	return ed.layout.clips.filter(it => it.c.id !== without && (it.start + it.end) / 2 < T).length;
}
function edShowDrop(index, without) {
	const items = ed.layout.clips.filter(it => it.c.id !== without);
	const x = !items.length ? edX(0) : index >= items.length ? edX(items[items.length - 1].end) : edX(items[index].start + items[index].overlapIn / 2);
	$('ed-drop').hidden = false; $('ed-drop').style.left = x + 'px';
}
$('ed-track').addEventListener('pointerdown', (e) => {
	if (!ed.layout || e.button !== 0 || e.target.closest('.tl-join')) return;
	edCloseMenu();
	const block = e.target.closest('.tl-clip'), bar = e.target.closest('.tl-text'), T = edTimeAt(e);
	try { $('ed-track').setPointerCapture(e.pointerId); } catch {}
	// a text's bar: drag to move it in time, its edges to change when it shows
	if (bar) {
		const t = edText(bar.dataset.text); if (!t) return;
		edPause();
		const handle = e.target.closest('.tl-handle');
		ed.drag = { kind: handle ? 'text-trim' : 'text-press', side: handle && handle.classList.contains('l') ? 'l' : 'r', id: t.id, x0: e.clientX, start: t.start, end: t.end, moved: false };
		if (handle && ed.textSel !== t.id) edSelectText(t.id, false);
		return;
	}
	if (!block) { edPause(); ed.drag = { kind: 'scrub' }; edSeek(T); return; }
	const it = edItem(block.dataset.id), handle = e.target.closest('.tl-handle');
	if (handle) {
		edPause();
		ed.drag = { kind: 'trim', side: handle.classList.contains('l') ? 'l' : 'r', id: it.c.id, x0: e.clientX, in: it.c.in, out: it.c.out, left: parseFloat(block.style.left), width: parseFloat(block.style.width), moved: false };
		if (ed.selected !== it.c.id) edSelect(it.c.id);
		return;
	}
	ed.drag = { kind: 'press', id: it.c.id, x0: e.clientX, T };
});
$('ed-track').addEventListener('pointermove', (e) => {
	const d = ed.drag; if (!d) return;
	if (d.kind === 'scrub') { edSeek(edTimeAt(e)); return; }
	if (d.kind === 'text-press' && Math.abs(e.clientX - d.x0) > 4) { d.kind = 'text-move'; if (ed.textSel !== d.id) edSelectText(d.id, false); }
	if (d.kind === 'text-move' || d.kind === 'text-trim') {
		const t = edText(d.id); if (!t) return;
		if (!d.moved) { edCheckpoint(); d.moved = true; }
		const shift = (e.clientX - d.x0) / ed.pps, length = d.end - d.start;
		if (d.kind === 'text-move') { t.start = Math.max(edSnapTime(d.start + shift, length), 0); t.end = t.start + length; }
		else if (d.side === 'l') t.start = Math.min(Math.max(edSnapTime(d.start + shift), 0), t.end - 0.2);
		else t.end = Math.max(edSnapTime(d.end + shift), t.start + 0.2);
		edRenderTimeline();
		// the picture at the edge that moves
		edSeek(d.kind === 'text-trim' ? (d.side === 'l' ? t.start : Math.max(t.end - 1 / edFps(), t.start)) : edTextSeekPoint(t));
		edSyncTextInspector(t);
		return;
	}
	if (d.kind === 'press' && Math.abs(e.clientX - d.x0) > 6) { d.kind = 'move'; edPause(); }
	if (d.kind === 'move') {
		const block = $('ed-track').querySelector(`.tl-clip[data-id="${d.id}"]`);
		if (block) { block.classList.add('dragging'); block.style.transform = `translateX(${e.clientX - d.x0}px)`; }
		d.index = edInsertIndex(edTimeAt(e), d.id);
		edShowDrop(d.index, d.id);
		return;
	}
	if (d.kind === 'trim') {
		const c = edClips().find(c => c.id === d.id), info = edInfo(c);
		if (!c) return;
		if (!d.moved) { edCheckpoint(); d.moved = true; }
		const shift = (e.clientX - d.x0) / ed.pps * c.speed, least = 0.1 * c.speed;
		if (d.side === 'r') {
			c.out = Math.min(Math.max(d.out + shift, c.in + least), info ? info.duration : Math.max(d.out, c.in + least));
			ed.layout = edLayout();
			edRenderTimeline();
			const it = edItem(c.id);
			edShowAt(ed.t = Math.max(it.end - 1 / edFps(), it.start));
		} else {
			// the start edge follows the mouse; the clips after it close up when the mouse is let go
			c.in = Math.min(Math.max(d.in + shift, 0), c.out - least);
			const moved = (c.in - d.in) / c.speed * ed.pps;
			const block = $('ed-track').querySelector(`.tl-clip[data-id="${d.id}"]`);
			ed.layout = edLayout();
			if (block) { block.style.left = d.left + moved + 'px'; block.style.width = Math.max(d.width - moved, 3) + 'px'; edDrawStrip(block.querySelector('canvas'), edItem(c.id)); }
			const it = edItem(c.id);
			edShowAt(ed.t = it.start);
			$('ed-playhead').style.left = d.left + moved + 'px';
		}
		edRenderTime();
	}
});
function edEndDrag(e) {
	const d = ed.drag; if (!d) return;
	ed.drag = null;
	$('ed-drop').hidden = true;
	if (d.kind === 'press') { edSelect(d.id); edSeek(d.T); }
	else if (d.kind === 'text-press') edSelectText(d.id);
	else if (d.kind === 'text-move' || d.kind === 'text-trim') { if (d.moved && edText(d.id)) edTextChanged(edText(d.id)); }
	else if (d.kind === 'move') {
		const from = edClips().findIndex(c => c.id === d.id);
		let to = d.index ?? from;
		if (to !== from && to !== from + 1 && e.type !== 'pointercancel') {
			if (to > from) to -= 1;
			edChange(() => { const [c] = edClips().splice(from, 1); edClips().splice(to, 0, c); });
			ed.selected = d.id;
			edSeek(edItem(d.id).start);
		}
		edRenderAll();
	}
	else if (d.kind === 'trim' && d.moved) { edChanged(); edScrollTo(ed.t); }
}
$('ed-track').addEventListener('pointerup', edEndDrag);
$('ed-track').addEventListener('pointercancel', edEndDrag);
// the time ruler: click to jump, drag to select a part; the playhead's knob scrubs
$('ed-ruler').addEventListener('pointerdown', (e) => {
	if (!ed.layout || !ed.layout.clips.length || e.button !== 0) return;
	edCloseMenu(); edPause();
	try { $('ed-ruler').setPointerCapture(e.pointerId); } catch {}
	const T = edTimeAt(e);
	ed.drag = { kind: 'ruler', a: T, x0: e.clientX };
	edSeek(T);
});
$('ed-ruler').addEventListener('pointermove', (e) => {
	const d = ed.drag; if (!d || (d.kind !== 'ruler' && d.kind !== 'range')) return;
	const T = edTimeAt(e);
	if (d.kind === 'ruler' && Math.abs(e.clientX - d.x0) > 4) { d.kind = 'range'; ed.selected = null; edRenderTimeline(); }
	if (d.kind === 'range') { ed.range = { a: Math.min(d.a, T), b: Math.max(d.a, T) }; edRenderRange(); edSeek(T); }
});
function edEndRuler() {
	const d = ed.drag; if (!d || (d.kind !== 'ruler' && d.kind !== 'range')) return;
	ed.drag = null;
	if (d.kind === 'ruler' || (ed.range && ed.range.b - ed.range.a < 0.1)) ed.range = null;
	else edSeek(ed.range.a);
	edRenderRange(); edRenderInspector(); edRenderTimeline();
}
$('ed-ruler').addEventListener('pointerup', edEndRuler);
$('ed-ruler').addEventListener('pointercancel', edEndRuler);
$('ed-playhead').firstElementChild.addEventListener('pointerdown', (e) => {
	e.stopPropagation(); edPause();
	const knob = e.currentTarget;
	try { knob.setPointerCapture(e.pointerId); } catch {}
	const move = (ev) => edSeek(edTimeAt(ev));
	knob.addEventListener('pointermove', move);
	knob.addEventListener('pointerup', () => knob.removeEventListener('pointermove', move), { once: true });
});
$('ed-scroll').addEventListener('wheel', (e) => {
	if (!ed.layout) return;
	if (e.ctrlKey) { e.preventDefault(); const T = edTimeAt(e); edZoomTo(ed.zoom * (e.deltaY < 0 ? 1.25 : 0.8), T, e.clientX - $('ed-scroll').getBoundingClientRect().left); }
	else if (Math.abs(e.deltaY) > Math.abs(e.deltaX) && ed.zoom > 1) { e.preventDefault(); $('ed-scroll').scrollLeft += e.deltaY; }
}, { passive: false });
// transitions: the round buttons between clips
$('ed-track').addEventListener('click', (e) => {
	const b = e.target.closest('.tl-join'); if (!b) return;
	const join = b.dataset.join, index = join === 'start' || join === 'end' ? join : +join;
	if (typeof index === 'number') edSelect(edClips()[index].id);
	// selecting redraws the timeline: the menu goes next to the new button
	edTransitionMenu($('ed-track').querySelector(`[data-join="${join}"]`) || b, index);
});
function edTransitionMenu(anchor, index) {
	const edge = typeof index === 'string' ? index : null;
	const kind = edge ? ((edge === 'start' ? ed.project.fade_in : ed.project.fade_out) ? 'black' : 'cut') : edClips()[index].transition?.type || 'cut';
	const duration = (edge ? (edge === 'start' ? ed.project.fade_in : ed.project.fade_out) : edClips()[index].transition?.duration) || 1;
	const options = edge ? [['cut', 'None'], ['black', edge === 'start' ? 'Fade in from black' : 'Fade out to black']] : ED_TRANSITIONS;
	edMenu(anchor, `<h5>${edge === 'start' ? 'Start of the video' : edge === 'end' ? 'End of the video' : `Between clip ${index} and ${index + 1}`}</h5>
		<div class="menu-list">${options.map(([k, name, about]) => `<button data-k="${k}" class="${k === kind ? 'active' : ''}"><span>${name}${about ? `<br><span class="menu-sub">${about}</span>` : ''}</span>${k === kind ? '✓' : ''}</button>`).join('')}</div>
		<div class="menu-row"><span class="small muted">Length</span><div class="choice sm">${ED_DURATIONS.map(d => `<button data-d="${d}" class="${kind !== 'cut' && d === duration ? 'active' : ''}" ${kind === 'cut' ? 'disabled' : ''}>${String(d).replace('0.5', '½')} s</button>`).join('')}</div></div>`,
	(target) => {
		const k = target.closest('[data-k]')?.dataset.k, d = target.closest('[data-d]')?.dataset.d;
		if (k) { edSetTransition(index, k, duration); edTransitionMenu(anchor.isConnected ? anchor : $('ed-track').querySelector(`[data-join="${index}"]`) || anchor, index); }
		else if (d) { edSetTransition(index, kind, +d); edTransitionMenu($('ed-track').querySelector(`[data-join="${index}"]`) || anchor, index); }
	});
}
function edMenu(anchor, html, onClick) {
	const menu = $('ed-menu');
	menu.innerHTML = html; menu.hidden = false;
	ed.menu = { onClick };
	const rect = anchor.getBoundingClientRect(), width = menu.offsetWidth, height = menu.offsetHeight;
	menu.style.left = Math.min(Math.max(rect.left + rect.width / 2 - width / 2, 8), window.innerWidth - width - 8) + 'px';
	menu.style.top = (rect.top - height - 8 > 8 ? rect.top - height - 8 : rect.bottom + 8) + 'px';
}
function edCloseMenu() { $('ed-menu').hidden = true; ed.menu = null; }
$('ed-menu').addEventListener('click', (e) => { if (ed.menu) ed.menu.onClick(e.target); });
document.addEventListener('pointerdown', (e) => { if (ed.menu && !e.target.closest('#ed-menu, .tl-join, #ed-edits, #ed-keys')) edCloseMenu(); });
// all keys, behind a button (the timeline keeps its height for the clips)
const ED_KEYS = [['Space', 'play / pause'], ['S', 'split at the playhead'], ['Delete', 'remove the selected clip, part or text'], ['← / →', 'one frame back / on (with Shift: 1 second)'],
	['Home / End', 'to the start / end'], ['I / O', 'select a part from / up to the playhead'], ['T', 'add a text at the playhead'], ['Enter', 'change the selected text'],
	['Ctrl+B / I / U', 'bold, italic, underline (a selected text)'], ['F', 'full screen'], ['+ / −', 'zoom the timeline in / out'], ['Ctrl+C / X / V', 'copy, cut, paste'],
	['Ctrl+Z / Y', 'undo / redo'], ['Ctrl+S', 'save the project file (with Shift: save as)'], ['Ctrl+O', 'open a project file'], ['Esc', 'clear the selection, close a menu'], ['Menu key', 'the right-click menu of what is selected (also Shift+F10)']];
$('ed-keys').onclick = () => {
	if (ed.menu) { edCloseMenu(); return; }
	edMenu($('ed-keys'), `<h5>Keys · right-click anything on the timeline for its menu</h5><div class="ed-keylist">${ED_KEYS.map(([keys, what]) => `<span>${keys.split(' / ').map(k => `<kbd>${k}</kbd>`).join(' / ')}</span><span>${what}</span>`).join('')}</div>`, () => {});
};

// dragging a video from the list onto the timeline
const ED_DRAG_TYPE = 'application/x-edit-video';
$('ed-scroll').addEventListener('dragover', (e) => {
	if (![...e.dataTransfer.types].includes(ED_DRAG_TYPE) || !ed.layout) return;
	e.preventDefault(); e.dataTransfer.dropEffect = 'copy';
	edShowDrop(edInsertIndex(edTimeAt(e)));
});
$('ed-scroll').addEventListener('dragleave', (e) => { if (!$('ed-scroll').contains(e.relatedTarget)) $('ed-drop').hidden = true; });
$('ed-scroll').addEventListener('drop', (e) => {
	const path = e.dataTransfer.getData(ED_DRAG_TYPE);
	$('ed-drop').hidden = true;
	if (!path) return;
	e.preventDefault();
	edAddVideo(path, edInsertIndex(edTimeAt(e)));
});

// ---------------------------------------------------------------- crop (drawn on the picture, in the video's own pixels)
function edCropStart(it) {
	const info = edInfo(it.c); if (!info) return;
	edPause();
	if (ed.t < it.start || ed.t >= it.end) ed.t = it.start + Math.min(it.len / 2, 1);
	edCheckpoint();
	ed.crop = { id: it.c.id, rect: it.c.crop ? { ...it.c.crop } : { x: 0, y: 0, w: info.width, h: info.height }, drag: null };
	edShowAt(ed.t);
	edPlaceCrop();
	edRenderInspector();
}
function edCropDone(keep) {
	const crop = ed.crop; if (!crop) return;
	ed.crop = null;
	$('ed-crop').hidden = true;
	const c = edClips().find(c => c.id === crop.id), info = c && edInfo(c);
	if (keep && c && info) {
		const r = crop.rect, x = Math.max(0, Math.round(r.x / 2) * 2), y = Math.max(0, Math.round(r.y / 2) * 2);
		const w = Math.min(Math.round(r.w / 2) * 2, Math.floor((info.width - x) / 2) * 2), h = Math.min(Math.round(r.h / 2) * 2, Math.floor((info.height - y) / 2) * 2);
		c.crop = w >= info.width - 1 && h >= info.height - 1 ? null : { x, y, w, h };
		edChanged();
	} else { ed.history.pop(); edShowAt(ed.t); edRenderInspector(); }
}
function edPlaceCrop() {
	const layer = $('ed-crop');
	const it = ed.crop && edItem(ed.crop.id);
	layer.hidden = !it;
	if (!it) return;
	const info = edInfo(it.c), box = edFrameBox(it.c, null);
	Object.assign(layer.style, { left: box.left + '%', top: box.top + '%', width: box.width + '%', height: box.height + '%' });
	const r = ed.crop.rect, rect = $('ed-crop-rect').style;
	rect.left = r.x / info.width * 100 + '%'; rect.top = r.y / info.height * 100 + '%'; rect.width = r.w / info.width * 100 + '%'; rect.height = r.h / info.height * 100 + '%';
}
function edCropPoint(e) {
	const rect = $('ed-crop').getBoundingClientRect(), info = edInfo(edItem(ed.crop.id).c);
	return { x: (e.clientX - rect.left) / rect.width * info.width, y: (e.clientY - rect.top) / rect.height * info.height };
}
function edSetCropRect(r) {
	const info = edInfo(edItem(ed.crop.id).c), min = 16;
	r.w = Math.max(min, Math.min(r.w, info.width)); r.h = Math.max(min, Math.min(r.h, info.height));
	r.x = Math.max(0, Math.min(r.x, info.width - r.w)); r.y = Math.max(0, Math.min(r.y, info.height - r.h));
	ed.crop.rect = r;
	edPlaceCrop();
}
$('ed-crop').addEventListener('pointerdown', (e) => {
	if (!ed.crop) return;
	const p = edCropPoint(e), corner = [...e.target.classList].find(name => ['nw', 'ne', 'sw', 'se'].includes(name));
	// the starting rectangle is the whole picture: dragging inside it draws a new one
	const info = edInfo(edItem(ed.crop.id).c), r = ed.crop.rect, whole = r.w >= info.width - 2 && r.h >= info.height - 2;
	ed.crop.drag = corner ? { kind: corner, start: p, rect: { ...r } } : e.target.id === 'ed-crop-rect' && !whole ? { kind: 'move', start: p, rect: { ...r } } : { kind: 'new', start: p };
	try { $('ed-crop').setPointerCapture(e.pointerId); } catch {}
	e.preventDefault();
});
$('ed-crop').addEventListener('pointermove', (e) => {
	const d = ed.crop && ed.crop.drag; if (!d) return;
	const p = edCropPoint(e), dx = p.x - d.start.x, dy = p.y - d.start.y;
	if (d.kind === 'new') edSetCropRect({ x: Math.min(p.x, d.start.x), y: Math.min(p.y, d.start.y), w: Math.abs(dx), h: Math.abs(dy) });
	else if (d.kind === 'move') edSetCropRect({ ...d.rect, x: d.rect.x + dx, y: d.rect.y + dy });
	else {
		let { x, y, w, h } = d.rect;
		if (d.kind.includes('w')) { x += dx; w -= dx; } else w += dx;
		if (d.kind.includes('n')) { y += dy; h -= dy; } else h += dy;
		edSetCropRect({ x, y, w: Math.max(w, 16), h: Math.max(h, 16) });
	}
});
$('ed-crop').addEventListener('pointerup', () => { if (ed.crop) ed.crop.drag = null; });

// ---------------------------------------------------------------- texts on the video
// A text is { id, text, start, end (edit time), x, y (where it sits, as a part of the frame: x is its left edge,
// middle or right edge, as it is aligned; y is its middle), font, size (pixels at a frame height of 1080), bold,
// italic, underline, color, align, box (colour of the band behind it, or null), box_opacity, outline (0 none, 1 thin,
// 2 thick), outline_color, shadow, fade (seconds in and out) }. One drawing function paints the texts on the player
// and makes the pictures the export lays over the video, so the exported video looks exactly like the preview.
const ED_FONTS = [
	['Clean', ['Segoe UI', 'Arial', 'Calibri', 'Verdana', 'Tahoma', 'Trebuchet MS', 'Corbel', 'Candara', 'Bahnschrift', 'Arial Narrow']],
	['Classic', ['Georgia', 'Times New Roman', 'Cambria', 'Constantia', 'Palatino Linotype', 'Sitka Heading']],
	['Bold and playful', ['Impact', 'Comic Sans MS', 'Gabriola']],
	['Handwritten', ['Segoe Script', 'Segoe Print', 'Ink Free']],
	['Typewriter', ['Courier New', 'Consolas', 'Lucida Console']]
];
const ED_TEXT_KEYS = ['font', 'size', 'bold', 'italic', 'underline', 'color', 'align', 'box', 'box_opacity', 'outline', 'outline_color', 'shadow'];
const ED_TEXT_STYLES = [
	['Title', 'Large, in the middle', { font: 'Segoe UI', size: 96, bold: true, italic: false, underline: false, color: '#ffffff', align: 'center', box: null, box_opacity: 0.6, outline: 0, outline_color: '#000000', shadow: true }, 'center'],
	['Subtitle', 'White on a dark band, at the bottom', { font: 'Segoe UI', size: 54, bold: false, italic: false, underline: false, color: '#ffffff', align: 'center', box: '#000000', box_opacity: 0.6, outline: 0, outline_color: '#000000', shadow: false }, 'bottom'],
	['Name', 'On a coloured band, bottom left', { font: 'Segoe UI', size: 50, bold: true, italic: false, underline: false, color: '#ffffff', align: 'left', box: '#2440a8', box_opacity: 0.9, outline: 0, outline_color: '#000000', shadow: false }, 'bottom-left'],
	['Big and bold', 'Thick letters with a black outline', { font: 'Impact', size: 150, bold: false, italic: false, underline: false, color: '#ffffff', align: 'center', box: null, box_opacity: 0.6, outline: 2, outline_color: '#000000', shadow: false }, 'center'],
	['Handwritten', 'Warm yellow handwriting', { font: 'Segoe Script', size: 84, bold: true, italic: false, underline: false, color: '#ffe27a', align: 'center', box: null, box_opacity: 0.6, outline: 0, outline_color: '#000000', shadow: true }, 'center'],
	['Date stamp', 'Like an old camcorder, bottom right', { font: 'Consolas', size: 46, bold: true, italic: false, underline: false, color: '#ffb31a', align: 'right', box: null, box_opacity: 0.6, outline: 0, outline_color: '#000000', shadow: true }, 'bottom-right']
];
const ED_PLACES = [['top-left', 'Top left'], ['top', 'Top'], ['top-right', 'Top right'], ['left', 'Left'], ['center', 'Middle'], ['right', 'Right'], ['bottom-left', 'Bottom left'], ['bottom', 'Bottom'], ['bottom-right', 'Bottom right']];
const ED_SWATCHES = ['#ffffff', '#000000', '#ffe27a', '#ffb31a', '#ff5a5a', '#ff8fd1', '#7ad7ff', '#2440a8', '#5fd38d'];
const ED_ALIGN = {
	left: edSvg('<path d="M4 6h16M4 10h10M4 14h16M4 18h10"/>'),
	center: edSvg('<path d="M4 6h16M7 10h10M4 14h16M7 18h10"/>'),
	right: edSvg('<path d="M4 6h16M10 10h10M4 14h16M10 18h10"/>')
};
const edTextCanvas = document.createElement('canvas').getContext('2d');
// fonts that are not on this PC are left out of the list (they would show as another font)
let edFontGroups = null;
function edFontList() {
	if (edFontGroups) return edFontGroups;
	const probe = 'mmmmmmmmmmlli1WQ@#&', width = (font) => { edTextCanvas.font = font; return edTextCanvas.measureText(probe).width; };
	const present = (font) => ['monospace', 'serif'].some(base => width(`40px "${font}", ${base}`) !== width(`40px ${base}`));
	return edFontGroups = ED_FONTS.map(([group, fonts]) => [group, fonts.filter(present)]).filter(([, fonts]) => fonts.length);
}
const edTextFont = (t, px) => `${t.italic ? 'italic ' : ''}${t.bold ? 'bold ' : ''}${px}px "${t.font}", "Segoe UI", sans-serif`;
function edRgba(hex, alpha) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16 & 255}, ${n >> 8 & 255}, ${n & 255}, ${alpha})`; }
// sizes in frame pixels: the font, the lines and the box around them
function edTextLayout(t, frame) {
	const px = t.size * frame.h / 1080, lineH = px * 1.22;
	edTextCanvas.font = edTextFont(t, px);
	const lines = (t.text || ' ').split('\n').map(line => ({ line, w: edTextCanvas.measureText(line).width }));
	const padX = t.box ? px * 0.4 : px * 0.12, padY = t.box ? px * 0.2 : px * 0.06;
	const w = Math.max(...lines.map(l => l.w), px * 0.3) + padX * 2, h = lines.length * lineH + padY * 2;
	const ax = t.x * frame.w, left = t.align === 'left' ? ax : t.align === 'right' ? ax - w : ax - w / 2;
	return { px, lineH, lines, padX, padY, w, h, left, top: t.y * frame.h - h / 2 };
}
function edDrawText(ctx, t, lay, alpha) {
	if (alpha <= 0) return;
	ctx.save();
	if (t.box) {
		ctx.globalAlpha = alpha * t.box_opacity;
		ctx.fillStyle = t.box;
		ctx.beginPath(); ctx.roundRect(lay.left, lay.top, lay.w, lay.h, lay.px * 0.16); ctx.fill();
	}
	ctx.globalAlpha = alpha;
	ctx.font = edTextFont(t, lay.px);
	ctx.textBaseline = 'middle';
	ctx.textAlign = t.align;
	ctx.lineJoin = 'round';
	const x = t.align === 'left' ? lay.left + lay.padX : t.align === 'right' ? lay.left + lay.w - lay.padX : lay.left + lay.w / 2;
	const shadow = (on) => { ctx.shadowColor = on ? 'rgba(0, 0, 0, .6)' : 'transparent'; ctx.shadowBlur = lay.px * 0.14; ctx.shadowOffsetY = lay.px * 0.05; };
	lay.lines.forEach(({ line, w }, i) => {
		const y = lay.top + lay.padY + (i + 0.5) * lay.lineH;
		// the shadow falls once, under the outline when there is one
		shadow(t.shadow);
		if (t.outline) { ctx.lineWidth = lay.px * (t.outline === 2 ? 0.16 : 0.08); ctx.strokeStyle = t.outline_color; ctx.strokeText(line, x, y); shadow(false); }
		ctx.fillStyle = t.color;
		ctx.fillText(line, x, y);
		if (t.underline && line.trim()) ctx.fillRect(t.align === 'left' ? x : t.align === 'right' ? x - w : x - w / 2, y + lay.px * 0.38, w, Math.max(lay.px * 0.065, 1));
	});
	ctx.restore();
}
function edTextAlpha(t, T) {
	if (T < t.start || T >= t.end) return 0;
	const fade = Math.min(t.fade || 0, (t.end - t.start) / 2);
	return fade ? Math.max(0, Math.min(1, (T - t.start) / fade, (t.end - T) / fade)) : 1;
}
// a moment where the text shows fully (after its fade in)
const edTextSeekPoint = (t) => Math.min(t.start + Math.min(t.fade || 0, (t.end - t.start) / 2) + 0.05, (t.start + t.end) / 2);
// the texts on the player, and the boxes to click and drag them by
function edPaintTexts(T) {
	const canvas = $('ed-texts'), stage = $('ed-stage'), L = ed.layout;
	const ratio = window.devicePixelRatio || 1, w = Math.round(stage.clientWidth * ratio), h = Math.round(stage.clientHeight * ratio);
	if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
	const ctx = canvas.getContext('2d');
	ctx.setTransform(1, 0, 0, 1, 0, 0);
	ctx.clearRect(0, 0, w, h);
	let ui = '';
	if (L && L.clips.length && !ed.crop && w) {
		const scale = w / L.frame.w;
		ctx.setTransform(scale, 0, 0, scale, 0, 0);
		for (const t of edTexts()) {
			if (T < t.start || T >= t.end) continue;
			const lay = edTextLayout(t, L.frame), selected = t.id === ed.textSel;
			if (t.id !== ed.textEdit) edDrawText(ctx, t, lay, edTextAlpha(t, T));
			ui += `<div class="ed-tbox${selected ? ' selected' : ''}${t.id === ed.textEdit ? ' editing' : ''}" data-text="${t.id}" style="left:${lay.left / L.frame.w * 100}%;top:${lay.top / L.frame.h * 100}%;width:${lay.w / L.frame.w * 100}%;height:${lay.h / L.frame.h * 100}%" title="${selected ? 'Drag to move · drag a corner to resize · double-click to change the text' : 'Click to select · double-click to change the text'}">${selected ? '<span class="h nw"></span><span class="h ne"></span><span class="h sw"></span><span class="h se"></span>' : ''}</div>`;
		}
	}
	const box = $('ed-text-ui');
	if (box.dataset.html !== ui) { box.innerHTML = ui; box.dataset.html = ui; }
}
// rows for the texts on the timeline: a text goes in the first row that is free at its time (at most 4 rows)
function edTextLanes() {
	const ends = [], of = {};
	for (const t of [...edTexts()].sort((a, b) => a.start - b.start)) {
		let lane = ends.findIndex(end => end <= t.start + 1e-6);
		if (lane < 0) lane = ends.length < 4 ? ends.length : ends.indexOf(Math.min(...ends));
		ends[lane] = t.end; of[t.id] = lane;
	}
	return { of, count: Math.max(ends.length, 1) };
}
let edTimelineSoon = 0;
function edRenderTimelineSoon() { if (!edTimelineSoon) edTimelineSoon = requestAnimationFrame(() => { edTimelineSoon = 0; edRenderTimeline(); }); }

// selecting, adding, changing and removing texts
function edSelectText(id, seek = true) {
	if (ed.textEdit && ed.textEdit !== id) edEndTextEdit();
	if (ed.crop) edCropDone(true);
	ed.textSel = id; ed.selected = null; ed.range = null;
	const t = edText(id);
	if (seek && t && (ed.t < t.start || ed.t >= t.end)) { edPause(); edSeek(edTextSeekPoint(t)); }
	edRenderTimeline(); edRenderRange(); edRenderInspector(); edPaintTexts(ed.t);
}
const edTextStyleOf = (t) => Object.fromEntries(ED_TEXT_KEYS.map(key => [key, t[key]]));
// a new text at the playhead (or for a part), with the look of the text before it, ready to type in
function edAddText(from, to, at) {
	if (!ed.layout || !ed.layout.clips.length) { toast('Add a video first, then put text on it'); return; }
	edPause();
	const total = ed.layout.total;
	let start = Math.max(from ?? ed.t, 0);
	if (start > total - 0.5) start = Math.max(total - 4, 0);
	const end = Math.max(Math.min(to ?? start + 4, total), start + 0.5);
	const last = edText(ed.textSel) || edTexts()[edTexts().length - 1];
	const t = { id: edId(), text: 'Your text', start, end, x: 0.5, y: 0.5, fade: last ? last.fade : 0.5, ...(last ? edTextStyleOf(last) : edTextStyleOf(ED_TEXT_STYLES[0][2])) };
	// where the text before it was, unless both show at once; or where the video was clicked
	if (at) { t.x = at.x; t.y = at.y; if (t.align !== 'center') { t.align = 'center'; } }
	else if (last && (last.end <= start || last.start >= end)) { t.x = last.x; t.y = last.y; }
	else if (last) { t.align = 'center'; }
	edChange(() => edTexts().push(t));
	edSelectText(t.id);
	edEditText(t.id, true);
}
// typing on the video: a text field over the text, in its font and size
function edEditText(id, fresh) {
	const t = edText(id); if (!t || !ed.layout) return;
	edPause();
	if (ed.textSel !== id) edSelectText(id, false);
	if (edTextAlpha(t, ed.t) < 1) edSeek(edTextSeekPoint(t));
	if (!fresh) edCheckpoint();
	ed.textEdit = id;
	const area = $('ed-text-edit');
	area.value = t.text; area.hidden = false;
	edPlaceTextEditor();
	area.focus({ preventScroll: true }); area.select();
	edPaintTexts(ed.t);
}
function edPlaceTextEditor() {
	const t = edText(ed.textEdit), area = $('ed-text-edit');
	if (!t || !ed.layout) return;
	const frame = ed.layout.frame, lay = edTextLayout(t, frame), s = $('ed-stage').clientWidth / frame.w, st = area.style;
	// room for the next letter on the side the text grows to
	const room = Math.max(lay.px * s * 0.7, 14), before = t.align === 'right' ? room : t.align === 'center' ? room / 2 : 0;
	st.left = lay.left * s - before + 'px'; st.top = lay.top * s + 'px';
	st.width = lay.w * s + room + 'px'; st.height = lay.h * s + 'px';
	st.font = edTextFont(t, lay.px * s);
	st.lineHeight = lay.lineH * s + 'px';
	st.padding = `${lay.padY * s}px ${lay.padX * s + (t.align === 'right' ? 0 : before)}px ${lay.padY * s}px ${lay.padX * s + before}px`;
	st.textAlign = t.align; st.color = t.color;
	st.textDecoration = t.underline ? 'underline' : 'none';
	st.background = t.box ? edRgba(t.box, t.box_opacity) : 'rgba(0, 0, 0, .3)';
	st.textShadow = t.shadow ? `0 ${lay.px * s * 0.05}px ${lay.px * s * 0.14}px rgba(0, 0, 0, .6)` : 'none';
	st.webkitTextStroke = t.outline ? `${lay.px * s * (t.outline === 2 ? 0.16 : 0.08)}px ${t.outline_color}` : '0';
}
// quiet: the text is about to be replaced (undo), so nothing else changes
function edEndTextEdit(quiet) {
	if (!ed.textEdit) return;
	const t = edText(ed.textEdit);
	ed.textEdit = null;
	$('ed-text-edit').hidden = true;
	if (quiet) return;
	if (t && !t.text.trim()) {
		edTexts().splice(edTexts().indexOf(t), 1);
		if (ed.textSel === t.id) ed.textSel = null;
		toast('The empty text was removed');
	}
	edScheduleSave();
	edRenderTimeline(); edRenderInspector(); edPaintTexts(ed.t);
}
// after a change to a text: saved, drawn again, and the timeline and the panel follow
function edTextChanged(t, live) {
	edScheduleSave();
	edPaintTexts(ed.t);
	if (ed.textEdit) edPlaceTextEditor();
	edRenderTimelineSoon();
	if (live) edSyncTextInspector(t); else edRenderInspector();
}
// a change from the panel; slide: one undo step for a whole slider drag or typing burst
function edTextChange(fn, slide) {
	const t = edText(ed.textSel); if (!t) return;
	if (!slide || !ed.sliding) edCheckpoint();
	if (slide) ed.sliding = true;
	fn(t);
	edTextChanged(t, slide);
}
function edDeleteText(id) {
	const i = edTexts().findIndex(t => t.id === id); if (i < 0) return;
	if (ed.textEdit === id) edEndTextEdit(true);
	edChange(() => { edTexts().splice(i, 1); if (ed.textSel === id) ed.textSel = null; });
	edPaintTexts(ed.t);
}
// the box keeps its place when the alignment changes
function edSetAlign(t, align) {
	const frame = ed.layout.frame, lay = edTextLayout(t, frame);
	t.align = align;
	t.x = (align === 'left' ? lay.left : align === 'right' ? lay.left + lay.w : lay.left + lay.w / 2) / frame.w;
}
// one of nine places, a little inside the edges (where TVs do not cut it off)
function edPlaceText(t, place) {
	const frame = ed.layout.frame, lay = edTextLayout(t, frame), w = lay.w / frame.w, h = lay.h / frame.h;
	const left = place.includes('left') ? 0.05 : place.includes('right') ? 0.95 - w : 0.5 - w / 2;
	t.x = t.align === 'left' ? left : t.align === 'right' ? left + w : left + w / 2;
	t.y = place.startsWith('top') ? 0.07 + h / 2 : place.startsWith('bottom') ? 0.93 - h / 2 : 0.5;
}
function edApplyTextStyle(id, index) {
	const t = edText(id); if (!t) return;
	const [, , style, place] = ED_TEXT_STYLES[index];
	edCheckpoint();
	Object.assign(t, JSON.parse(JSON.stringify(style)));
	edPlaceText(t, place);
	edTextChanged(t);
}
function edTextTime(id, side) {
	const t = edText(id); if (!t) return;
	if (side === 'start' ? ed.t >= t.end - 0.1 : ed.t <= t.start + 0.1) { toast(side === 'start' ? 'Put the playhead before the end of the text' : 'Put the playhead after the start of the text'); return; }
	edCheckpoint();
	t[side] = ed.t;
	edTextChanged(t);
}
// the text shows exactly as long as the clip it starts on
function edTextMatchClip(id) {
	const t = edText(id); if (!t || !ed.layout) return;
	const it = edActiveAt(Math.min(t.start + 0.01, ed.layout.total))[0]; if (!it) return;
	edCheckpoint();
	t.start = it.start + it.overlapIn / 2; t.end = it.end - it.overlapOut / 2;
	edTextChanged(t);
}
function edTextToFront(id) {
	const i = edTexts().findIndex(t => t.id === id); if (i < 0) return;
	edChange(() => edTexts().push(...edTexts().splice(i, 1)));
}
function edDuplicateText(id) {
	const t = edText(id); if (!t) return;
	const copy = { ...JSON.parse(JSON.stringify(t)), id: edId(), start: t.end, end: t.end + (t.end - t.start) };
	edChange(() => edTexts().push(copy));
	edSelectText(copy.id);
}

// the text field on the video
$('ed-text-edit').addEventListener('input', () => {
	const t = edText(ed.textEdit); if (!t) return;
	t.text = $('ed-text-edit').value;
	edTextChanged(t, true);
});
$('ed-text-edit').addEventListener('blur', () => edEndTextEdit());
$('ed-text-edit').addEventListener('keydown', (e) => {
	const t = edText(ed.textEdit), key = e.key.toLowerCase();
	if (e.key === 'Escape' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) { e.preventDefault(); $('ed-text-edit').blur(); }
	else if ((e.ctrlKey || e.metaKey) && t && ['b', 'i', 'u'].includes(key)) {
		e.preventDefault();
		const style = { b: 'bold', i: 'italic', u: 'underline' }[key];
		t[style] = !t[style];
		edTextChanged(t, true);
	}
});
// on the player: click selects, drag moves (it snaps to the middle), a corner resizes, double-click edits
$('ed-text-ui').addEventListener('pointerdown', (e) => {
	const el = e.target.closest('.ed-tbox'); if (!el || e.button !== 0) return;
	const t = edText(el.dataset.text); if (!t) return;
	e.preventDefault(); e.stopPropagation();
	edPause();
	if (ed.textSel !== t.id) edSelectText(t.id, false);
	const stage = $('ed-stage').getBoundingClientRect(), frame = ed.layout.frame, lay = edTextLayout(t, frame), s = stage.width / frame.w;
	const center = { x: stage.left + (lay.left + lay.w / 2) * s, y: stage.top + (lay.top + lay.h / 2) * s };
	const drag = { resize: !!e.target.closest('.h'), x0: e.clientX, y0: e.clientY, x: t.x, y: t.y, size: t.size, moved: false };
	drag.dist = Math.max(Math.hypot(e.clientX - center.x, e.clientY - center.y), 4);
	const move = (ev) => {
		if (!drag.moved) { if (Math.hypot(ev.clientX - drag.x0, ev.clientY - drag.y0) < 3) return; edCheckpoint(); drag.moved = true; }
		let snapX = false, snapY = false;
		if (drag.resize) t.size = Math.round(Math.min(Math.max(drag.size * Math.hypot(ev.clientX - center.x, ev.clientY - center.y) / drag.dist, 12), 400));
		else {
			t.x = Math.min(Math.max(drag.x + (ev.clientX - drag.x0) / stage.width, -0.2), 1.2);
			t.y = Math.min(Math.max(drag.y + (ev.clientY - drag.y0) / stage.height, 0), 1);
			const now = edTextLayout(t, frame), cx = (now.left + now.w / 2) / frame.w;
			if (Math.abs(cx - 0.5) < 0.015) { t.x += 0.5 - cx; snapX = true; }
			if (Math.abs(t.y - 0.5) < 0.015) { t.y = 0.5; snapY = true; }
		}
		$('ed-guide-v').hidden = !snapX; $('ed-guide-h').hidden = !snapY;
		edTextChanged(t, true);
	};
	const up = () => {
		document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); document.removeEventListener('pointercancel', up);
		$('ed-guide-v').hidden = $('ed-guide-h').hidden = true;
		if (drag.moved) edTextChanged(t);
	};
	document.addEventListener('pointermove', move); document.addEventListener('pointerup', up); document.addEventListener('pointercancel', up);
});
$('ed-text-ui').addEventListener('dblclick', (e) => { const el = e.target.closest('.ed-tbox'); if (el) edEditText(el.dataset.text); });
$('ed-track').addEventListener('dblclick', (e) => { const bar = e.target.closest('.tl-text'); if (bar) edEditText(bar.dataset.text); });
// moving a text on the timeline: its start or end snaps to the edges of clips
function edSnapTime(T, length = 0) {
	const points = [0, ed.layout.total, ...ed.layout.clips.map(it => it.start + it.overlapIn / 2)];
	let best = T, gap = 8 / ed.pps;
	for (const p of points) {
		if (Math.abs(p - T) < gap) { gap = Math.abs(p - T); best = p; }
		if (length && Math.abs(p - T - length) < gap) { gap = Math.abs(p - T - length); best = p - length; }
	}
	return best;
}

// the panel on the right for a text
function edFontOptions(current) {
	const groups = edFontList();
	let html = groups.map(([group, fonts]) => `<optgroup label="${group}">${fonts.map(f => `<option value="${esc(f)}" style="font-family:'${esc(f)}'">${esc(f)}</option>`).join('')}</optgroup>`).join('');
	if (!groups.some(([, fonts]) => fonts.includes(current))) html += `<option value="${esc(current)}">${esc(current)}</option>`;
	return html;
}
function edStyleCss(style) {
	return `font-family:'${style.font}';font-weight:${style.bold ? 700 : 400};color:${style.color};${style.box ? `background:${edRgba(style.box, style.box_opacity)};` : ''}${style.outline ? `-webkit-text-stroke:${style.outline}px ${style.outline_color};paint-order:stroke fill;` : ''}${style.shadow ? 'text-shadow:0 1px 3px rgba(0,0,0,.7);' : ''}`;
}
function edInspectorText(t) {
	const choice = (key, options) => `<div class="choice sm">${options.map(([v, name, title]) => `<button data-tx-set="${key}" data-v="${v}"${title ? ` title="${title}"` : ''}>${name}</button>`).join('')}</div>`;
	return `<div class="insp-head"><div style="min-width:0"><p class="eyebrow">Text</p><h3 id="insp-tx-title"></h3><div class="small muted num" id="insp-tx-when"></div></div>
			<button class="icon-btn danger" data-tx-act="delete" title="Remove this text (Delete)">${ED_ICON.trash}</button></div>
		<div class="insp-sec"><textarea class="input tx-area" data-tx="text" rows="2" placeholder="Type your text" aria-label="The text"></textarea>
			<div class="insp-note">Or double-click the text on the video and type there. Drag it to move it, drag a corner to resize it.</div></div>
		<div class="insp-sec"><div class="insp-label">Quick styles</div>
			<div class="tx-styles">${ED_TEXT_STYLES.map(([name, about, style], i) => `<button data-tx-act="style" data-v="${i}" title="${about}"><span style="${edStyleCss(style)}">${name}</span></button>`).join('')}</div></div>
		<div class="insp-sec"><div class="insp-label">Font</div>
			<select class="input tx-font" data-tx="font" aria-label="Font">${edFontOptions(t.font)}</select>
			<div class="row tx-row"><div class="choice sm"><button data-tx-toggle="bold" title="Bold (Ctrl+B)"><b>B</b></button><button data-tx-toggle="italic" title="Italic (Ctrl+I)"><i style="font-family:Georgia,serif">I</i></button><button data-tx-toggle="underline" title="Underline (Ctrl+U)"><u>U</u></button></div>
				${choice('align', ['left', 'center', 'right'].map(a => [a, ED_ALIGN[a], `Align ${a === 'center' ? 'in the middle' : a}`]))}</div>
			<div class="insp-label" style="margin-top:12px">Size <span class="num" id="insp-tx-size"></span></div>
			<div class="row" style="flex-wrap:nowrap"><button class="icon-btn" data-tx-act="smaller" title="Smaller">−</button><input type="range" data-tx="size" min="16" max="300" step="2" aria-label="Size"><button class="icon-btn" data-tx-act="bigger" title="Bigger">+</button></div></div>
		<div class="insp-sec"><div class="insp-label">Colour</div>
			<div class="tx-swatches">${ED_SWATCHES.map(c => `<button data-tx-set="color" data-v="${c}" style="background:${c}" title="${c}" aria-label="Colour ${c}"></button>`).join('')}<label class="tx-custom" title="Any colour"><input type="color" data-tx="color" aria-label="Any colour"></label></div></div>
		<div class="insp-sec"><div class="insp-label">Background</div>
			<div class="row tx-row" style="margin-top:0">${choice('boxmode', [['none', 'None'], ['box', 'Band behind the text']])}</div>
			<div class="row tx-row" data-show="box"><input type="color" data-tx="box" title="Colour of the band" aria-label="Colour of the band"><span class="small muted">See-through</span><input type="range" data-tx="box_opacity" min="0.1" max="1" step="0.05" style="flex:1" aria-label="How solid the band is"></div></div>
		<div class="insp-sec"><div class="insp-label">Outline and shadow</div>
			<div class="row tx-row" style="margin-top:0">${choice('outline', [[0, 'No outline'], [1, 'Thin'], [2, 'Thick']])}<input type="color" data-tx="outline_color" data-show="outline" title="Colour of the outline" aria-label="Colour of the outline"></div>
			<label class="check" style="margin-top:10px"><span class="switch"><input type="checkbox" data-tx="shadow"><span></span></span><span><b>Shadow</b><small>Keeps text readable on light and busy pictures</small></span></label></div>
		<div class="insp-sec"><div class="insp-label">Place on the picture</div>
			<div class="row" style="flex-wrap:nowrap;align-items:flex-start"><div class="tx-places">${ED_PLACES.map(([p, name]) => `<button data-tx-act="place" data-v="${p}" title="${name}" aria-label="${name}"><span></span></button>`).join('')}</div>
			<div class="insp-note" style="margin:0">Or drag the text on the video: it snaps to the middle.</div></div></div>
		<div class="insp-sec"><div class="insp-label">When it shows</div>
			<div class="row"><button class="btn sm" data-tx-act="start-here" title="The text appears at the playhead">Starts here</button><button class="btn sm" data-tx-act="end-here" title="The text disappears at the playhead">Ends here</button><button class="btn sm quiet" data-tx-act="match-clip" title="As long as the clip it starts on">Whole clip</button></div>
			<div class="row tx-row"><span class="small muted">Fade in and out</span>${choice('fade', [[0, 'None'], [0.5, '½ s'], [1, '1 s']])}</div>
			<div class="insp-note">Or drag its bar on the timeline, or the bar's edges.</div></div>`;
}
// the panel shows the text's current settings (the field being typed in or dragged is left alone)
function edSyncTextInspector(t) {
	const box = $('ed-inspector');
	if (box.dataset.text !== t.id) return;
	for (const el of box.querySelectorAll('[data-tx]')) {
		if (el === document.activeElement && el.type !== 'checkbox' && el.tagName !== 'SELECT') continue;
		const key = el.dataset.tx;
		if (el.type === 'checkbox') el.checked = !!t[key];
		else el.value = key === 'box' ? t.box || '#000000' : t[key];
	}
	for (const b of box.querySelectorAll('[data-tx-toggle]')) b.classList.toggle('active', !!t[b.dataset.txToggle]);
	for (const b of box.querySelectorAll('[data-tx-set]')) b.classList.toggle('active', (b.dataset.txSet === 'boxmode' ? (t.box ? 'box' : 'none') : String(t[b.dataset.txSet])) === b.dataset.v);
	for (const el of box.querySelectorAll('[data-show]')) el.hidden = el.dataset.show === 'box' ? !t.box : !t.outline;
	$('insp-tx-title').textContent = t.text.split('\n')[0] || 'Text';
	$('insp-tx-size').textContent = t.size;
	$('insp-tx-when').textContent = `${clock(t.start, 1)} – ${clock(t.end, 1)} · ${clock(t.end - t.start, 1)}`;
	box.querySelector('[data-tx-act="start-here"]').disabled = ed.t >= t.end - 0.1;
	box.querySelector('[data-tx-act="end-here"]').disabled = ed.t <= t.start + 0.1;
}
$('ed-inspector').addEventListener('click', (e) => {
	const b = e.target.closest('[data-tx-act], [data-tx-set], [data-tx-toggle]');
	const t = edText(ed.textSel);
	if (!b || b.disabled || !t) return;
	if (b.dataset.txToggle) { const key = b.dataset.txToggle; edTextChange(t => { t[key] = !t[key]; }); return; }
	if (b.dataset.txSet) {
		const key = b.dataset.txSet, v = b.dataset.v;
		edTextChange(t => {
			if (key === 'boxmode') t.box = v === 'box' ? t.box || '#000000' : null;
			else if (key === 'align') edSetAlign(t, v);
			else if (key === 'outline' || key === 'fade') t[key] = +v;
			else t[key] = v;
		});
		return;
	}
	const act = b.dataset.txAct;
	if (act === 'delete') edDeleteText(t.id);
	else if (act === 'style') edApplyTextStyle(t.id, +b.dataset.v);
	else if (act === 'place') edTextChange(t => edPlaceText(t, b.dataset.v));
	else if (act === 'smaller' || act === 'bigger') edTextChange(t => { t.size = Math.min(Math.max(Math.round(t.size * (act === 'bigger' ? 1.15 : 1 / 1.15) / 2) * 2, 12), 400); });
	else if (act === 'start-here' || act === 'end-here') edTextTime(t.id, act === 'start-here' ? 'start' : 'end');
	else if (act === 'match-clip') edTextMatchClip(t.id);
});
$('ed-inspector').addEventListener('input', (e) => {
	const el = e.target, key = el.dataset.tx;
	if (!key || !edText(ed.textSel)) return;
	edTextChange(t => {
		if (key === 'text') t.text = el.value;
		else if (key === 'size' || key === 'box_opacity') t[key] = +el.value;
		else if (key === 'shadow') t.shadow = el.checked;
		else if (key === 'font') t.font = el.value;
		else t[key] = el.value.toLowerCase();
	}, !['shadow', 'font'].includes(key));
});
$('ed-inspector').addEventListener('change', (e) => { if (e.target.dataset.tx) ed.sliding = false; });

// ---------------------------------------------------------------- right-click menus
// Like the menus of video editors: what can be done with the clip, transition, selected part, text or place that
// was right-clicked. An item is { label, icon, keys, value, note, checked, disabled, danger, run }, or
// { label, menu: [items] } (a menu that opens to the side), { label, chips: [items] } (a row of small buttons),
// { head, detail } or '-'. Arrow keys, Enter and Esc work as in any menu; the menu key or Shift+F10 opens one.
const edCtx = { panels: [], opened: 0, timer: null };
function edContextMenu(x, y, items) {
	edCloseContext(); edCloseMenu(); edPause();
	edCtx.opened = Date.now();
	edCtxPanel(items, 0, { x, y }).focus({ preventScroll: true });
}
function edCtxPanel(items, level, at) {
	edCtxCloseFrom(level);
	const list = [];
	for (const item of items.filter(Boolean)) if (item !== '-' || (list.length && list[list.length - 1] !== '-' && !list[list.length - 1].head)) list.push(item);
	if (list[list.length - 1] === '-') list.pop();
	const el = document.createElement('div');
	el.className = 'ed-ctx'; el.setAttribute('role', 'menu'); el.tabIndex = -1;
	el.innerHTML = list.map(edCtxRow).join('');
	// in full screen only the full-screen part of the page shows
	(document.fullscreenElement || document.body).appendChild(el);
	const w = el.offsetWidth, h = el.offsetHeight, W = window.innerWidth, H = window.innerHeight;
	let left = at.x, top = at.y;
	if (at.from) {
		const row = at.from.getBoundingClientRect(), panel = at.from.closest('.ed-ctx').getBoundingClientRect();
		left = panel.right + w - 3 <= W - 6 ? panel.right - 3 : panel.left - w + 3;
		top = row.top - 5;
	} else {
		if (left + w > W - 6) left -= w;
		if (top + h > H - 6) top -= h;
	}
	el.style.left = Math.max(Math.min(left, W - w - 6), 6) + 'px';
	el.style.top = Math.max(Math.min(top, H - h - 6), 6) + 'px';
	edCtx.panels[level] = { el, items: list, from: at.from || null };
	return el;
}
function edCtxRow(item, i) {
	if (item === '-') return '<div class="ctx-sep" role="separator"></div>';
	if (item.head) return `<div class="ctx-head" title="${esc(item.head)}">${esc(item.head)}${item.detail ? `<small>${esc(item.detail)}</small>` : ''}</div>`;
	if (item.chips) return `<div class="ctx-chips"><span>${esc(item.label)}</span>${item.chips.map((chip, j) => `<button class="ctx-chip${chip.checked ? ' on' : ''}" data-i="${i}" data-j="${j}" ${chip.disabled ? 'disabled' : ''}>${esc(chip.label)}</button>`).join('')}</div>`;
	return `<button class="ctx-item${item.checked ? ' checked' : ''}${item.danger ? ' danger' : ''}" role="menuitem" data-i="${i}" ${item.disabled ? 'disabled' : ''}${item.menu ? ' aria-haspopup="menu"' : ''}>`
		+ `<span class="ctx-icon">${item.icon || ''}</span><span class="ctx-label">${esc(item.label)}${item.note ? `<small>${esc(item.note)}</small>` : ''}</span>`
		+ `<span class="ctx-end">${item.value ? `<span class="ctx-value">${esc(item.value)}</span>` : ''}${item.keys ? `<kbd>${esc(item.keys)}</kbd>` : ''}${item.checked ? ED_ICON.check : ''}${item.menu ? '<span class="ctx-arrow">›</span>' : ''}</span></button>`;
}
function edCtxFind(el) {
	const level = edCtx.panels.findIndex(p => p.el === el.closest('.ed-ctx'));
	if (level < 0) return null;
	const item = edCtx.panels[level].items[+el.dataset.i];
	return item ? { level, item: el.dataset.j != null ? item.chips[+el.dataset.j] : item } : null;
}
function edCtxOpen(el, found, focus) {
	if (edCtx.panels[found.level + 1]?.from !== el) {
		edCtxPanel(found.item.menu, found.level + 1, { from: el });
		el.classList.add('open');
	}
	if (focus) edCtx.panels[found.level + 1].el.querySelector('button:not(:disabled)')?.focus({ preventScroll: true });
}
function edCtxCloseFrom(level) {
	for (const p of edCtx.panels.splice(level)) p.el.remove();
	edCtx.panels[level - 1]?.el.querySelector('.ctx-item.open')?.classList.remove('open');
}
function edCloseContext() { clearTimeout(edCtx.timer); if (edCtx.panels.length) edCtxCloseFrom(0); }
// keys while a menu is open; false: the key closed the menu and does what it does without one
function edCtxKey(e) {
	const key = e.key, active = document.activeElement;
	if (['Shift', 'Control', 'Alt', 'Meta'].includes(key)) return true;
	let level = edCtx.panels.findIndex(p => p.el === active?.closest?.('.ed-ctx'));
	if (level < 0) level = edCtx.panels.length - 1;
	const buttons = [...edCtx.panels[level].el.querySelectorAll('button:not(:disabled)')], at = buttons.indexOf(active);
	if (key === 'ArrowDown' || key === 'ArrowUp') {
		const step = key === 'ArrowDown' ? 1 : -1;
		buttons[at < 0 ? (step > 0 ? 0 : buttons.length - 1) : (at + step + buttons.length) % buttons.length]?.focus({ preventScroll: true });
	}
	else if (key === 'ArrowRight') { if (at >= 0 && active.getAttribute('aria-haspopup')) edCtxOpen(active, edCtxFind(active), true); }
	else if (key === 'ArrowLeft' || (key === 'Escape' && level > 0)) { if (level > 0) { const from = edCtx.panels[level].from; edCtxCloseFrom(level); from?.focus({ preventScroll: true }); } }
	else if (key === 'Escape' || key === 'Tab') edCloseContext();
	else if (key === 'Enter' || key === ' ') { if (at >= 0) active.click(); }
	else { edCloseContext(); return false; }
	e.preventDefault();
	return true;
}
document.addEventListener('click', (e) => {
	const el = e.target.closest?.('.ed-ctx button');
	if (!el || el.disabled) return;
	const found = edCtxFind(el); if (!found) return;
	if (found.item.menu) { edCtxOpen(el, found, !e.detail); return; }
	edCloseContext();
	found.item.run();
});
// pointing at an item: a menu to the side opens after a moment, and closes when the mouse moves on to another item
document.addEventListener('pointerover', (e) => {
	if (!e.target.closest?.('.ed-ctx')) return;
	clearTimeout(edCtx.timer);
	const el = e.target.closest('button'); if (!el) return;
	const found = edCtxFind(el); if (!found) return;
	if (!el.disabled) el.focus({ preventScroll: true });
	edCtx.timer = setTimeout(() => {
		if (edCtx.panels[found.level]?.el !== el.closest('.ed-ctx')) return;
		if (found.item.menu && !el.disabled) edCtxOpen(el, found, false);
		else edCtxCloseFrom(found.level + 1);
	}, 170);
});
document.addEventListener('pointerout', (e) => {
	const from = e.target.closest?.('.ed-ctx button');
	if (from && document.activeElement === from && !e.relatedTarget?.closest?.('.ed-ctx button') && !from.classList.contains('open')) from.closest('.ed-ctx').focus({ preventScroll: true });
});
document.addEventListener('pointerdown', (e) => { if (edCtx.panels.length && !e.target.closest?.('.ed-ctx')) edCloseContext(); }, true);
document.addEventListener('contextmenu', (e) => { if (e.target.closest?.('.ed-ctx') || Date.now() - edCtx.opened < 500) e.preventDefault(); });
window.addEventListener('blur', () => edCloseContext());
const edCloseOnScroll = () => { if (Date.now() - edCtx.opened > 400) edCloseContext(); };
$('ed-scroll').addEventListener('scroll', edCloseOnScroll);
window.addEventListener('scroll', edCloseOnScroll, { passive: true });

// what goes in the menus
function edTransitionValue(index) {
	if (index === 'start') return ed.project.fade_in ? 'Fade in' : 'None';
	if (index === 'end') return ed.project.fade_out ? 'Fade out' : 'None';
	return ED_TRANSITIONS.find(t => t[0] === (edClips()[index].transition?.type || 'cut'))[1];
}
function edTransitionItems(index) {
	const edge = typeof index === 'string' ? index : null;
	const amount = edge ? (edge === 'start' ? ed.project.fade_in : ed.project.fade_out) : null;
	const kind = edge ? (amount ? 'black' : 'cut') : edClips()[index].transition?.type || 'cut';
	const duration = (edge ? amount : edClips()[index].transition?.duration) || 1;
	const options = edge ? [['cut', 'None'], ['black', edge === 'start' ? 'Fade in from black' : 'Fade out to black']] : ED_TRANSITIONS;
	return [
		...options.map(([k, name, about]) => ({ label: name, note: about, icon: edge ? (k === 'black' ? ED_ICON.fadeEdge : '') : ED_ICON[k], checked: k === kind, run: () => edSetTransition(index, k, duration) })),
		'-',
		{ label: 'Length', chips: ED_DURATIONS.map(d => ({ label: String(d).replace('0.5', '½') + ' s', checked: kind !== 'cut' && d === duration, disabled: kind === 'cut', run: () => edSetTransition(index, kind, d) })) }
	];
}
function edJoinItem(i, keep, label, whose) {
	const a = edClips()[i], b = edClips()[i + 1];
	if (!a || !b) return null;
	const other = keep === a ? b : a;
	const differs = [['speed', keep.speed !== other.speed], ['sound', keep.sound !== other.sound], ['brightness', (keep.gamma || 1) !== (other.gamma || 1)], ['crop', JSON.stringify(keep.crop) !== JSON.stringify(other.crop)]].filter(d => d[1]).map(d => d[0]);
	const can = edCanJoin(i);
	return { label, icon: ED_ICON.join, disabled: !can, note: !can ? 'for parts of a video that were split' : differs.length ? `keeps ${whose} ${differs.join(', ')}` : '', run: () => edJoin(i, keep) };
}
const edSpeedItems = (current, run) => ED_SPEEDS.map(s => ({ label: s === 1 ? '1× (normal)' : edSpeedLabel(s), note: s === 3 ? 'sound goes off above 2×' : '', checked: s === current, run: () => run(s) }));
// "Insert a video": the videos in the list on the left, or any other video on this PC
function edVideoItems(index) {
	const lib = ed.library, entries = lib ? [...lib.results, ...lib.queue, ...lib.used] : [];
	return [
		...entries.slice(0, 12).map(entry => ({ label: entry.name, value: entry.duration ? clock(entry.duration) : '', run: () => edAddVideo(entry.path, index) })),
		'-',
		{ label: 'Choose a video on this PC…', icon: ED_ICON.add, run: () => edPickVideos(index) }
	];
}
function edClipMenu(it, x, y) {
	if (ed.selected !== it.c.id || ed.textSel) edSelect(it.c.id);
	const c = it.c, n = ed.layout.clips.length, info = edInfo(c), hasSound = !info || info.has_audio;
	const here = edSplitTarget(ed.t) === it, inside = ed.t > it.start + 0.02 && ed.t < it.end - 0.02;
	const into = it.i ? it.i : 'start', out = it.i < n - 1 ? it.i + 1 : 'end';
	edContextMenu(x, y, [
		{ head: c.name, detail: `Clip ${it.i + 1} of ${n} · ${clock(c.in, 1)} – ${clock(c.out, 1)} of the video` },
		{ label: 'Split here', icon: ED_ICON.split, keys: 'S', disabled: !here, note: here ? '' : 'not inside a transition', run: edSplit },
		it.i > 0 && edJoinItem(it.i - 1, c, 'Join with the clip before', "this clip's"),
		it.i < n - 1 && edJoinItem(it.i, c, 'Join with the next clip', "this clip's"),
		'-',
		{ label: 'Speed', icon: ED_ICON.speed, value: edSpeedLabel(c.speed), menu: edSpeedItems(c.speed, (s) => edSetClipSpeed(c.id, s)) },
		{ label: 'Sound', icon: c.sound !== false && hasSound ? ED_ICON.sound : ED_ICON.muted, value: !hasSound ? 'none' : c.sound !== false ? 'on' : 'off', disabled: !hasSound, run: () => edChange(() => { c.sound = c.sound === false; }) },
		{ label: it.i ? 'Transition from the clip before' : 'Start of the video', icon: it.i ? ED_ICON[c.transition?.type || 'cut'] : ED_ICON.fadeEdge, value: edTransitionValue(into), menu: edTransitionItems(into) },
		{ label: it.i < n - 1 ? 'Transition to the next clip' : 'End of the video', icon: it.i < n - 1 ? ED_ICON[edClips()[it.i + 1].transition?.type || 'cut'] : ED_ICON.fadeEdge, value: edTransitionValue(out), menu: edTransitionItems(out) },
		'-',
		{ label: 'Trim the start to here', icon: ED_ICON.trimStart, disabled: !inside, run: () => edTrimTo(c.id, 'start') },
		{ label: 'Trim the end to here', icon: ED_ICON.trimEnd, disabled: !inside, run: () => edTrimTo(c.id, 'end') },
		'-',
		{ label: 'Add text here', icon: ED_ICON.text, keys: 'T', run: () => edAddText() },
		{ label: 'Insert a video after this clip', icon: ED_ICON.add, menu: edVideoItems(it.i + 1) },
		'-',
		{ label: 'Cut', keys: 'Ctrl+X', run: () => edCut(c.id) },
		{ label: 'Copy', icon: ED_ICON.copy, keys: 'Ctrl+C', run: () => edCopy(c.id) },
		{ label: ed.clipboard?.kind === 'text' ? 'Paste the text here' : 'Paste after this clip', icon: ED_ICON.paste, keys: 'Ctrl+V', disabled: !ed.clipboard, run: () => edPaste(it.i + 1) },
		{ label: 'Duplicate', icon: ED_ICON.duplicate, run: () => edDuplicate(c.id) },
		'-',
		{ label: 'Delete clip', icon: ED_ICON.trash, keys: 'Del', danger: true, run: () => { ed.selected = c.id; ed.range = null; ed.textSel = null; edDelete(); } }
	]);
}
function edJoinMenu(index, x, y) {
	const edge = typeof index === 'string';
	if (!edge) edSelect(edClips()[index].id);
	const it = edge ? null : ed.layout.clips[index];
	edContextMenu(x, y, [
		{ head: index === 'start' ? 'Start of the video' : index === 'end' ? 'End of the video' : `Between clip ${index} and ${index + 1}` },
		...edTransitionItems(index),
		'-',
		!edge && edJoinItem(index - 1, edClips()[index - 1], 'Join the two clips', "the first clip's"),
		!edge && it.kind !== 'cut' && { label: 'Play the transition', icon: ED_ICON.play, run: () => edPlayFrom(it.start - 1.5, Math.min(ed.layout.total, it.start + it.overlapIn + it.fadeIn + 1.5)) }
	]);
}
function edRangeMenu(x, y) {
	const { a, b } = ed.range, inside = ed.layout.clips.filter(it => it.end > a + 0.03 && it.start < b - 0.03), speeds = new Set(inside.map(it => it.c.speed));
	const current = speeds.size === 1 ? [...speeds][0] : null;
	edContextMenu(x, y, [
		{ head: 'Selected part', detail: `${clock(a, 1)} – ${clock(b, 1)} · ${clock(b - a, 1)} long` },
		{ label: 'Play this part', icon: ED_ICON.play, run: () => edPlayFrom(a, b) },
		'-',
		{ label: 'Speed of this part', icon: ED_ICON.speed, value: current ? edSpeedLabel(current) : 'mixed', menu: edSpeedItems(current, edRangeSpeed) },
		{ label: 'Split at both ends', icon: ED_ICON.split, note: 'the part becomes a clip of its own', run: edRangeSplit },
		{ label: 'Add text during this part', icon: ED_ICON.text, run: () => { ed.range = null; edAddText(a, b); } },
		'-',
		{ label: 'Cut this part out', icon: ED_ICON.trash, keys: 'Del', danger: true, run: () => edRangeDelete(false) },
		{ label: 'Keep only this part', icon: ED_ICON.part, run: () => edRangeDelete(true) },
		'-',
		{ label: 'Clear the selection', keys: 'Esc', run: () => { ed.range = null; edRenderRange(); edRenderInspector(); } }
	]);
}
function edTextMenu(t, x, y) {
	if (ed.textSel !== t.id) edSelectText(t.id, false);
	edContextMenu(x, y, [
		{ head: t.text.split('\n')[0] || 'Text', detail: `Text · ${clock(t.start, 1)} – ${clock(t.end, 1)}` },
		{ label: 'Change the text', icon: ED_ICON.edit, keys: 'Enter', run: () => edEditText(t.id) },
		{ label: 'Quick style', icon: ED_ICON.text, menu: ED_TEXT_STYLES.map(([name, about], i) => ({ label: name, note: about, run: () => edApplyTextStyle(t.id, i) })) },
		{ label: 'Place on the picture', icon: ED_ICON.move, menu: ED_PLACES.map(([place, name]) => ({ label: name, run: () => { edCheckpoint(); edPlaceText(t, place); edTextChanged(t); } })) },
		{ label: 'Size', chips: [['Smaller', 1 / 1.25], ['Bigger', 1.25]].map(([name, f]) => ({ label: name, run: () => { edCheckpoint(); t.size = Math.min(Math.max(Math.round(t.size * f / 2) * 2, 12), 400); edTextChanged(t); } })) },
		{ label: 'Style', chips: [['B', 'bold'], ['I', 'italic'], ['U', 'underline'], ['Shadow', 'shadow']].map(([name, key]) => ({ label: name, checked: !!t[key], run: () => { edCheckpoint(); t[key] = !t[key]; edTextChanged(t); } })) },
		'-',
		{ label: 'Starts at the playhead', icon: ED_ICON.trimStart, disabled: ed.t >= t.end - 0.1, run: () => edTextTime(t.id, 'start') },
		{ label: 'Ends at the playhead', icon: ED_ICON.trimEnd, disabled: ed.t <= t.start + 0.1, run: () => edTextTime(t.id, 'end') },
		{ label: 'As long as its clip', icon: ED_ICON.clock, run: () => edTextMatchClip(t.id) },
		{ label: 'Fade in and out', chips: [[0, 'None'], [0.5, '½ s'], [1, '1 s']].map(([v, name]) => ({ label: name, checked: (t.fade || 0) === v, run: () => { edCheckpoint(); t.fade = v; edTextChanged(t); } })) },
		'-',
		{ label: 'Bring to front', icon: ED_ICON.front, disabled: edTexts()[edTexts().length - 1] === t, run: () => edTextToFront(t.id) },
		{ label: 'Cut', keys: 'Ctrl+X', run: () => edCut(t.id) },
		{ label: 'Copy', icon: ED_ICON.copy, keys: 'Ctrl+C', run: () => edCopy(t.id) },
		{ label: 'Duplicate', icon: ED_ICON.duplicate, note: 'right after it', run: () => edDuplicateText(t.id) },
		'-',
		{ label: 'Delete text', icon: ED_ICON.trash, keys: 'Del', danger: true, run: () => edDeleteText(t.id) }
	]);
}
// the time ruler or an empty part of the timeline
function edPlaceMenu(x, y, onRuler) {
	const L = ed.layout, has = L.clips.length > 0;
	edContextMenu(x, y, [
		has && { label: 'Play from here', icon: ED_ICON.play, keys: 'Space', run: () => edPlay() },
		edSplitTarget(ed.t) && { label: 'Split here', icon: ED_ICON.split, keys: 'S', run: edSplit },
		has && { label: 'Add text here', icon: ED_ICON.text, keys: 'T', run: () => edAddText() },
		ed.clipboard && { label: 'Paste here', icon: ED_ICON.paste, keys: 'Ctrl+V', run: () => edPaste(edInsertIndex(ed.t)) },
		'-',
		onRuler && has && { label: 'Select a part from here', icon: ED_ICON.part, keys: 'I', run: () => edMark('a') },
		onRuler && has && { label: 'Select a part up to here', icon: ED_ICON.part, keys: 'O', run: () => edMark('b') },
		'-',
		{ label: has ? 'Insert a video here' : 'Add a video', icon: ED_ICON.add, menu: edVideoItems(has ? edInsertIndex(ed.t) : 0) },
		'-',
		{ label: 'Undo', icon: ED_ICON.undo, keys: 'Ctrl+Z', disabled: !ed.history.length, run: edUndo },
		{ label: 'Redo', icon: ED_ICON.redo, keys: 'Ctrl+Y', disabled: !ed.future.length, run: edRedo },
		has && { label: 'Show the whole edit', icon: ED_ICON.fit, disabled: ed.zoom === 1, run: () => edZoomTo(1) }
	]);
}
// the player: texts, or a new text where it was clicked
function edStageMenu(e) {
	const box = e.target.closest('.ed-tbox');
	if (box) { edTextMenu(edText(box.dataset.text), e.clientX, e.clientY); return; }
	const L = ed.layout; if (!L || !L.clips.length || ed.crop) return;
	const stage = $('ed-stage').getBoundingClientRect(), at = { x: (e.clientX - stage.left) / stage.width, y: (e.clientY - stage.top) / stage.height };
	edContextMenu(e.clientX, e.clientY, [
		{ label: 'Add text here', icon: ED_ICON.text, keys: 'T', run: () => edAddText(null, null, at) },
		edSplitTarget(ed.t) && { label: 'Split at the playhead', icon: ED_ICON.split, keys: 'S', run: edSplit },
		{ label: ed.playing ? 'Pause' : 'Play', icon: ed.playing ? ED_ICON.pause : ED_ICON.play, keys: 'Space', run: edToggle }
	]);
}
// a right-click on a clip or the ruler moves the playhead there (near the playhead it stays), so the picture shows
// where a split or trim will be
$('ed-scroll').addEventListener('contextmenu', (e) => {
	e.preventDefault();
	if (!ed.layout || (ed.drag && ed.drag.kind !== 'press' && ed.drag.kind !== 'text-press')) return;
	ed.drag = null;
	const T = edTimeAt(e), join = e.target.closest('.tl-join'), bar = e.target.closest('.tl-text'), block = e.target.closest('.tl-clip');
	const moveHere = () => { edPause(); if (Math.abs(T - ed.t) * ed.pps > 6) edSeek(T); };
	if (join) edJoinMenu(join.dataset.join === 'start' || join.dataset.join === 'end' ? join.dataset.join : +join.dataset.join, e.clientX, e.clientY);
	else if (bar) edTextMenu(edText(bar.dataset.text), e.clientX, e.clientY);
	else if (ed.range && T >= ed.range.a && T <= ed.range.b && (block || e.target.closest('#ed-ruler'))) edRangeMenu(e.clientX, e.clientY);
	else if (block) { moveHere(); edClipMenu(edItem(block.dataset.id), e.clientX, e.clientY); }
	else { if (ed.layout.clips.length) moveHere(); edPlaceMenu(e.clientX, e.clientY, !!e.target.closest('#ed-ruler')); }
});
$('ed-stage').addEventListener('contextmenu', (e) => {
	if (e.target.closest('#ed-text-edit')) return;
	e.preventDefault();
	edStageMenu(e);
});
// the menu key or Shift+F10: the menu of what is selected, at the playhead
function edKeyboardMenu() {
	if (!ed.layout) return;
	const text = edText(ed.textSel), it = edItem(ed.selected);
	if (text) edScrollTo(text.start);
	else if (ed.range) edScrollTo(ed.range.a);
	else {
		if (it && (ed.t < it.start || ed.t > it.end)) edSeek(it.start + it.len / 2);
		edScrollTo(ed.t);
	}
	const inner = $('ed-inner').getBoundingClientRect(), track = $('ed-track').getBoundingClientRect();
	const x = (T) => inner.left + edX(T), clipsY = track.top + 70;
	if (text) edTextMenu(text, x(text.start) + 8, track.top + edTextTop() + 20);
	else if (ed.range) edRangeMenu(x(ed.range.a) + 8, clipsY);
	else if (it) edClipMenu(it, x(ed.t), clipsY);
	else edPlaceMenu(x(ed.t), clipsY, false);
	edCtx.panels[0]?.el.querySelector('button:not(:disabled)')?.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- keys
document.addEventListener('keydown', (e) => {
	if (state.tab !== 'edit' || document.querySelector('dialog[open]')) return;
	if (edCtx.panels.length && edCtxKey(e)) return;
	if ((e.ctrlKey || e.metaKey) && !e.altKey && ['s', 'o'].includes(e.key.toLowerCase())) { e.preventDefault(); e.key.toLowerCase() === 'o' ? edOpenFile() : edSaveFile(e.shiftKey); return; }
	const tag = e.target.tagName;
	if ((tag === 'INPUT' && !['range', 'checkbox'].includes(e.target.type)) || tag === 'SELECT' || tag === 'TEXTAREA') return;
	const key = e.key, ctrl = e.ctrlKey || e.metaKey, lower = key.toLowerCase(), text = edText(ed.textSel);
	if (ctrl && lower === 'z') e.shiftKey ? edRedo() : edUndo();
	else if (ctrl && lower === 'y') edRedo();
	else if (ctrl && (lower === 'c' || lower === 'x') && (ed.textSel || ed.selected) && !String(window.getSelection())) lower === 'c' ? edCopy(ed.textSel || ed.selected) : edCut(ed.textSel || ed.selected);
	else if (ctrl && lower === 'v' && ed.clipboard) edPaste();
	else if (ctrl && text && ['b', 'i', 'u'].includes(lower)) { const style = { b: 'bold', i: 'italic', u: 'underline' }[lower]; edTextChange(t => { t[style] = !t[style]; }); }
	else if (key === 'ContextMenu' || (key === 'F10' && e.shiftKey)) edKeyboardMenu();
	else if (ctrl || e.altKey) return;
	else if (key === ' ') edToggle();
	else if (key === 's' || key === 'S') edSplit();
	else if (key === 't' || key === 'T') edAddText();
	else if (key === 'f' || key === 'F') edToggleFull();
	else if (key === 'Enter' && text && tag !== 'BUTTON') edEditText(text.id);
	else if (key === 'Delete' || key === 'Backspace') edDelete();
	else if (key === 'ArrowLeft' || key === 'ArrowRight') { if (tag === 'INPUT') return; edPause(); const step = e.shiftKey ? 1 : 1 / edFps(); edSeek(ed.t + (key === 'ArrowLeft' ? -step : step)); }
	else if (key === 'Home') edSeek(0);
	else if (key === 'End') edSeek(ed.layout ? ed.layout.total : 0);
	else if (key === 'i' || key === 'I') edMark('a');
	else if (key === 'o' || key === 'O') edMark('b');
	else if (key === 'Escape') edEscape();
	else if (key === '+' || key === '=') edZoomBy(1.5);
	else if (key === '-') edZoomBy(1 / 1.5);
	else return;
	e.preventDefault();
});
// mouse clicks leave buttons focused; Space must keep meaning play/pause
$('edit-tab').addEventListener('click', (e) => { if (e.detail && e.target.closest('button')) e.target.closest('button').blur(); });

// ---------------------------------------------------------------- transport and toolbar
$('ed-play').onclick = edToggle;
$('ed-start').onclick = () => edSeek(0);
$('ed-end').onclick = () => edSeek(ed.layout ? ed.layout.total : 0);
$('ed-back').onclick = () => { edPause(); edSeek(ed.t - 1 / edFps()); };
$('ed-forward').onclick = () => { edPause(); edSeek(ed.t + 1 / edFps()); };
$('ed-mute').onclick = () => { ed.muted = !ed.muted; edRenderTransport(); edShowAt(ed.t); };
$('ed-volume').oninput = () => { ed.volume = +$('ed-volume').value; ed.muted = false; edRenderTransport(); if (!ed.playing) edShowAt(ed.t); };
$('ed-split').onclick = edSplit;
$('ed-delete').onclick = edDelete;
$('ed-text-add').onclick = () => edAddText();
$('ed-undo').onclick = edUndo;
$('ed-redo').onclick = edRedo;
$('ed-zoom-in').onclick = () => edZoomBy(1.5);
$('ed-zoom-out').onclick = () => edZoomBy(1 / 1.5);
$('ed-zoom-fit').onclick = () => edZoomTo(1);
$('ed-zoom').oninput = () => edZoomTo(Math.pow(edMaxZoom(), +$('ed-zoom').value / 100));
$('ed-name').addEventListener('change', () => { if (!ed.project) return; ed.project.name = $('ed-name').value.trim() || 'Edit'; $('ed-name').value = ed.project.name; edScheduleSave(); });
$('ed-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('ed-name').blur(); });
// a click on the picture plays or pauses; with a text selected it first lets go of the text
$('ed-stage').addEventListener('click', (e) => {
	if (ed.crop || e.target.closest('#ed-crop, .ed-tbox, #ed-text-edit')) return;
	if (ed.textSel) { edEndTextEdit(); ed.textSel = null; edRenderTimeline(); edRenderInspector(); edPaintTexts(ed.t); return; }
	edToggle();
});

// the list of edits
$('ed-edits').onclick = async () => {
	if (ed.menu) { edCloseMenu(); return; }
	await edSaveNow();
	const { edits } = await api('/api/edit/list', {});
	edMenu($('ed-edits'), `<h5>Your edits · each is saved automatically</h5><div class="menu-list">${edits.map(e => `<button data-open="${e.id}" class="${e.id === ed.project?.id ? 'active' : ''}"><span>${esc(e.name)}<br><span class="menu-sub">${e.clips} clip${e.clips === 1 ? '' : 's'} · ${clock(e.duration)} · ${new Date(e.updated * 1000).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}${e.file ? ' · ' + esc(e.file.split(/[\\/]/).pop()) : ''}</span></span>${e.id === ed.project?.id ? '✓' : `<span class="icon-btn danger" data-remove="${e.id}" title="Delete this edit (the videos themselves stay)">${ED_ICON.trash}</span>`}</button>`).join('')}</div>
		<div class="menu-row"><button class="btn sm primary" data-new="1">New edit</button><button class="btn sm" data-open-file="1" title="Ctrl+O">Open project file…</button></div>
		<div class="menu-row"><button class="btn sm" data-save-file="1" title="Ctrl+S">Save project file</button><button class="btn sm quiet" data-save-as="1" title="Ctrl+Shift+S">Save as…</button></div>
		<div class="menu-sub" style="margin:6px 2px 0">${ed.project?.file ? `This edit's project file: ${esc(ed.project.file)}` : 'A project file (.vedit) is a copy of the edit wherever you want it, to open again later. The videos are not in it: keep them where they are, or move them along with it.'}</div>`,
	async (target) => {
		const remove = target.closest('[data-remove]')?.dataset.remove, open = target.closest('[data-open]')?.dataset.open;
		if (target.closest('[data-open-file]')) { edCloseMenu(); edOpenFile(); return; }
		if (target.closest('[data-save-file], [data-save-as]')) { edCloseMenu(); edSaveFile(!!target.closest('[data-save-as]')); return; }
		try {
			if (remove) {
				if (!confirm('Delete this edit? The videos it uses are not touched.')) return;
				await api('/api/edit/delete', { id: remove }); edCloseMenu(); $('ed-edits').click();
			} else if (target.closest('[data-new]')) { edCloseMenu(); edSetProject(await api('/api/edit/new', {})); $('ed-name').select(); }
			else if (open && open !== ed.project?.id) { edCloseMenu(); edSetProject(await api('/api/edit/open', { id: open })); }
			else edCloseMenu();
		} catch (error) { toast(error.message); }
	});
};

// ---------------------------------------------------------------- the list of videos
async function edLoadLibrary(force) {
	if (!force && Date.now() - ed.libraryTime < 5000) return;
	ed.libraryTime = Date.now();
	try { ed.library = await api('/api/edit/library', {}); edRenderLibrary(); } catch {}
}
function edRenderLibrary() {
	const lib = ed.library; if (!lib) return;
	const item = (entry) => `<div class="lib-item" draggable="true" data-path="${esc(entry.path)}" title="${esc(entry.path)}">
		<div class="lib-thumb" style="${entry.thumb ? `background-image:url('${media(entry.thumb)}')` : ''}"></div>
		<div style="min-width:0"><div class="lib-name">${esc(entry.name)}</div><div class="lib-meta num">${entry.duration ? clock(entry.duration) : '…'}${entry.width ? ` · ${entry.width}×${entry.height}` : ''}${entry.size ? ' · ' + mb(entry.size) : ''}</div></div>
		<button class="icon-btn add" data-act="add" title="Add to the end of the timeline">+</button></div>`;
	const section = (title, entries, empty) => `<h4>${title}</h4>${entries.length ? entries.map(item).join('') : `<div class="lib-empty">${empty}</div>`}`;
	const html = section('Results', lib.results, 'Finished upscaled videos appear here.') + (lib.queue.length ? section('Upscale queue', lib.queue, '') : '') + (lib.used.length ? section('Used in edits', lib.used, '') : '');
	if ($('ed-library').dataset.html !== html) { $('ed-library').innerHTML = html; $('ed-library').dataset.html = html; }
	edRenderStart();
}
// an empty edit: the player area says what to do and offers the videos there are, one click each
function edRenderStart() {
	const lib = ed.library;
	const entries = lib ? [ ...lib.queue, ...lib.results, ...lib.used ].slice(0, 6) : [];
	const html = entries.length ? entries.map(entry => `<button class="ed-start-item" data-path="${esc(entry.path)}" title="${esc(entry.path)}">
			<span class="lib-thumb" style="${entry.thumb ? `background-image:url('${media(entry.thumb)}')` : ''}"></span>
			<span style="min-width:0"><span class="lib-name">${esc(entry.name)}</span><span class="lib-meta num">${entry.duration ? clock(entry.duration) : ''}${entry.width ? ` · ${entry.width}×${entry.height}` : ''}</span></span>
			<span class="ed-start-plus">+ Add</span></button>`).join('')
		: '<div class="small" style="color:var(--ink-quiet)">No videos yet: add videos on the Upscale tab, or pick any video on this PC.</div>';
	if ($('ed-start-list').dataset.html !== html) { $('ed-start-list').innerHTML = html; $('ed-start-list').dataset.html = html; }
}
$('ed-start-list').addEventListener('click', (e) => { const b = e.target.closest('[data-path]'); if (b) edAddVideo(b.dataset.path); });
$('ed-start-add').onclick = () => $('ed-add').click();
$('ed-library').addEventListener('click', (e) => {
	const row = e.target.closest('.lib-item');
	if (row && e.target.closest('[data-act="add"]')) edAddVideo(row.dataset.path);
});
$('ed-library').addEventListener('dblclick', (e) => { const row = e.target.closest('.lib-item'); if (row && !e.target.closest('button')) edAddVideo(row.dataset.path); });
$('ed-library').addEventListener('dragstart', (e) => {
	const row = e.target.closest('.lib-item'); if (!row) return;
	e.dataTransfer.setData(ED_DRAG_TYPE, row.dataset.path);
	e.dataTransfer.effectAllowed = 'copy';
});
// any video on this PC, at the end of the timeline or at a place in it
async function edPickVideos(index) {
	toast('Choose videos in the window that opened…', true);
	try {
		const { files } = await api('/api/browse', { mode: 'files', pick: true });
		$('toast').classList.remove('show');
		for (const [k, file] of files.entries()) await edAddVideo(file.path, index == null ? null : index + k);
		edLoadLibrary(true);
	} catch (error) { toast(error.message); }
}
$('ed-add').onclick = () => edPickVideos();
// video files dropped on the page while the Edit tab is open: copied into the input folder, then added
async function edAddDropped(files) {
	await ed.ready;
	for (let i = 0; i < files.length; i++) {
		const { file, path } = files[i];
		try {
			const result = await upload(file, path, (f) => toast(`Copying ${i + 1} of ${files.length} · ${path} · ${Math.round(f * 100)}%`, true));
			await edAddVideo(result.path);
		} catch (error) { toast(`${path}: ${error.message}`); }
	}
	$('toast').classList.remove('show');
	edLoadLibrary(true);
}

// ---------------------------------------------------------------- export
function edSizeHint() {
	const size = segValue('ed-size') || 'original', format = $('ed-format').value;
	$('ed-format-field').hidden = size === 'original';
	if (size === 'original') { $('ed-size-hint').innerHTML = 'Largest file (H.264, about 9 Mbit/s at 1080p). Best if you want to edit it further.'; return; }
	const e = expected(size, format); if (!e) return;
	$('ed-size-hint').innerHTML = `${meter(e.vmaf)}<b>about ${e.reduction}% smaller</b> than full quality · VMAF ${e.vmaf}: ${e.label}<br>${COMPAT[format]}`;
}
function edOpenExport() {
	const L = ed.layout; if (!L || !L.clips.length) return;
	edPause();
	const s = state.data ? state.data.settings : {};
	$('ed-export-name').value = ed.project.name;
	setSeg('ed-size', s.size || 'original');
	$('ed-format').value = s.format || 'av1';
	$('ed-audio').checked = s.audio !== false;
	edSizeHint();
	$('ed-export-info').innerHTML = `<b class="num">${L.frame.w}×${L.frame.h} · ${+L.fps.toFixed(2)} fps · ${clock(L.total, 1)}</b> · ${L.clips.length} clip${L.clips.length > 1 ? 's' : ''}<br>
		Frame size and frame rate come from the clips: the largest clip of the shape most of the edit has; other clips get black bars. Making videos larger or smoother is done in the Upscale tab.<br>
		Exporting again after a small change only makes the parts that changed.`;
	$('ed-export-dialog').showModal();
}
// the texts as the export lays them over the video: each drawn at the size of the exported frame, by the same code
// that draws them on the player, with where it goes in the frame
function edTextPictures() {
	const L = ed.layout;
	return edTexts().filter(t => t.text.trim() && t.end > t.start && t.start < L.total).map(t => {
		const lay = edTextLayout(t, L.frame), margin = Math.ceil(lay.px * 0.35);
		const x = Math.floor(lay.left) - margin, y = Math.floor(lay.top) - margin;
		const canvas = document.createElement('canvas');
		canvas.width = Math.ceil(lay.left + lay.w) + margin - x; canvas.height = Math.ceil(lay.top + lay.h) + margin - y;
		const ctx = canvas.getContext('2d');
		ctx.translate(-x, -y);
		edDrawText(ctx, t, lay, 1);
		return { id: t.id, png: canvas.toDataURL('image/png'), x, y, frame: [L.frame.w, L.frame.h] };
	});
}
$('ed-export').onclick = edOpenExport;

// ---------------------------------------------------------------- project files
// The edit is kept by the app all the time (work\_ui\edits); a project file (.vedit) is a copy of it where you want
// it, to open again later. Saving again writes to the same file; opening it replaces the edit that came from it.
const edFileName = () => ed.project?.file ? ed.project.file.split(/[\\/]/).pop() : '';
async function edSaveFile(choose) {
	if (!ed.project) return;
	edEndTextEdit();
	clearTimeout(ed.saveTimer);
	if (choose || !ed.project.file) toast('Choose where to save the project in the window that opened…', true);
	try {
		const result = await api('/api/edit/save-file', { project: ed.project, as: !!choose });
		$('toast').classList.remove('show');
		if (result.cancelled) { edSetSaved(ed.savedText); return; }
		ed.project.file = result.project.file; ed.project.file_saved = result.project.file_saved;
		ed.fileDirty = false;
		edSetSaved('All changes saved');
		toast('Saved as ' + result.path);
	} catch (error) { toast('The project file could not be saved: ' + error.message); }
}
async function edOpenFile() {
	await ed.ready;
	await edSaveNow();
	toast('Choose a project file in the window that opened…', true);
	try { edOpened(await api('/api/edit/open-file', {})); }
	catch (error) { toast(error.message); }
}
// a .vedit file dropped on the page (see the drop handler in index.html)
async function edOpenDroppedProject(file) {
	await ed.ready;
	await edSaveNow();
	try { edOpened(await api('/api/edit/open-file', { text: await file.text(), name: file.name }), true); }
	catch (error) { toast(error.message); }
}
function edOpened(result, dropped) {
	$('toast').classList.remove('show');
	if (result.cancelled) return;
	edSetProject(result.project);
	edLoadLibrary(true);
	// a dropped file does not say where it is, so videos moved along with it are only found with Open project file…
	const where = dropped ? '; Edits ▾ → Open project file… also looks next to the project file' : ' (red on the timeline)';
	toast(result.missing ? `Opened ${result.project.name}: ${result.missing} video${result.missing === 1 ? ' is' : 's are'} not where the project says${where}` : `Opened ${result.project.name}`);
}
$('ed-save-file').onclick = () => edSaveFile(false);
$('ed-export-close').onclick = () => $('ed-export-dialog').close();
$('ed-size').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { setSeg('ed-size', b.dataset.value); edSizeHint(); } });
$('ed-format').onchange = edSizeHint;
$('ed-export-start').onclick = async () => {
	const size = segValue('ed-size') || 'original', format = $('ed-format').value, audio = $('ed-audio').checked;
	try {
		// the same File size and Sound settings as the Upscale tab: changed there too
		setSeg('size', size); $('format').value = format; $('audio').checked = audio; labelSliders();
		await saveSettingsNow();
		ed.project.name = $('ed-export-name').value.trim() || ed.project.name;
		$('ed-name').value = ed.project.name;
		clearTimeout(ed.saveTimer);
		const job = await api('/api/edit/export', { project: ed.project, texts: edTextPictures() });
		edSetSaved('All changes saved');
		$('ed-export-dialog').close();
		toast(`Exporting ${job.name} — the progress shows at the top`);
		ed.jobSeen = null;
		refresh();
	} catch (error) { toast(error.message); }
};
// called by the main page's refresh() every second
function edOnState(data) {
	const job = data.edit && data.edit.export, box = $('ed-job');
	if (state.tab === 'edit') { edLoadLibrary(false); edRenderWaitProgress(); }
	// a finished export shows until it is hidden, for at most a quarter of an hour
	const over = job && job.status !== 'running' && (ed.jobSeen === job.id + job.status || Date.now() / 1000 - job.finished > 900);
	if (!job || over) { if (!box.hidden) { box.hidden = true; edFitStage(); } return; }
	const appeared = box.hidden;
	box.hidden = false;
	box.className = 'ed-job' + (job.status === 'done' ? ' done' : job.status === 'error' ? ' error' : '');
	const elapsed = Date.now() / 1000 - job.started, left = job.fraction > 0.03 ? elapsed / job.fraction * (1 - job.fraction) : null;
	const r = job.result;
	const html = job.status === 'running'
		? `<div style="min-width:0"><b>Exporting ${esc(job.name)}</b> <span class="small muted num">· ${Math.round(job.fraction * 100)}% · ${esc(job.detail)}${left != null ? ` · about ${human(left)} left` : ''}</span><div class="bar"><div style="width:${job.fraction * 100}%"></div></div></div><button class="btn sm danger" data-job="stop">Stop</button>`
		: job.status === 'done'
		? `<div style="min-width:0"><b>Exported ${esc(job.name)}</b> <span class="small muted num">· ${mb(r.size)} · ${r.width}×${r.height} · ${clock(r.duration, 1)} · took ${human(job.finished - job.started)}${r.reused ? ` (${r.reused} of ${r.pieces} parts reused)` : ''}</span></div><div class="row" style="flex-wrap:nowrap"><button class="btn sm" data-job="play">${ICON.play} Play</button><button class="btn sm quiet" data-job="show">Show in folder</button><button class="icon-btn" data-job="hide" title="Hide">✕</button></div>`
		: `<div style="min-width:0"><b>${job.status === 'cancelled' ? 'Export stopped' : 'Export failed'}</b> <span class="small">${esc(job.error || '')}</span></div><button class="icon-btn" data-job="hide" title="Hide">✕</button>`;
	if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; }
	if (appeared && state.tab === 'edit') edFitStage();
	box.onclick = (e) => {
		const act = e.target.closest('[data-job]')?.dataset.job;
		if (act === 'stop') api('/api/edit/stop', {});
		else if (act === 'play') openViewer(job.name, job.output, null);
		else if (act === 'show') api('/api/reveal', { path: job.output });
		else if (act === 'hide') { ed.jobSeen = job.id + job.status; box.hidden = true; edFitStage(); }
	};
}

window.addEventListener('resize', () => { edCloseContext(); edApplyLibraryFold(); if (state.tab === 'edit') { edRenderTimeline(); edFitStage(); } });

// ---------------------------------------------------------------- room for the video
// the video list folds into a rail; folded by default on narrower windows, until it is opened or folded by hand
function edApplyLibraryFold() {
	let saved = null;
	try { saved = localStorage.getItem('ed-lib'); } catch {}
	$('edit-tab').classList.toggle('lib-folded', saved ? saved === 'folded' : window.innerWidth < 1280);
}
function edFoldLibrary(folded) {
	try { localStorage.setItem('ed-lib', folded ? 'folded' : 'open'); } catch {}
	edApplyLibraryFold();
}
$('ed-lib-close').onclick = () => edFoldLibrary(true);
$('ed-lib-open').onclick = () => edFoldLibrary(false);
$('ed-rail-add').onclick = () => edPickVideos();
edApplyLibraryFold();
// full screen: the player with its controls (F, the button, or a double-click on the video)
const edViewer = document.querySelector('.ed-viewer');
const ED_FULL_ICON = { enter: edSvg('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'), leave: edSvg('<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>') };
const edIsFull = () => document.fullscreenElement === edViewer;
function edToggleFull() {
	if (edIsFull()) document.exitFullscreen().catch(() => {});
	else edViewer.requestFullscreen().catch((error) => toast('Full screen is not available: ' + error.message));
}
function edRenderFull() {
	const full = edIsFull();
	$('ed-full').innerHTML = full ? ED_FULL_ICON.leave : ED_FULL_ICON.enter;
	$('ed-full').title = full ? 'Leave full screen (F or Esc)' : 'Full screen (F, or double-click the video)';
}
document.addEventListener('fullscreenchange', () => { edRenderFull(); edCloseContext(); edWakeControls(); requestAnimationFrame(() => { edFitStage(); edRenderTimeline(); }); });
$('ed-full').onclick = edToggleFull;
$('ed-stage').addEventListener('dblclick', (e) => { if (!ed.crop && !e.target.closest('#ed-crop, .ed-tbox, #ed-text-edit')) edToggleFull(); });
edRenderFull();
// in full screen the controls fade out while it plays and the mouse rests
let edIdleTimer = 0;
function edWakeControls() {
	edViewer.classList.remove('idle');
	clearTimeout(edIdleTimer);
	if (edIsFull() && ed.playing) edIdleTimer = setTimeout(() => { if (ed.playing && edIsFull()) edViewer.classList.add('idle'); }, 2500);
}
edViewer.addEventListener('pointermove', edWakeControls);
// the seek bar, shown in full screen (where the timeline is out of sight)
function edRenderSeek() { if (!ed.seekDrag && ed.layout) $('ed-seek').value = ed.layout.total ? Math.round(ed.t / ed.layout.total * 1000) : 0; }
$('ed-seek').addEventListener('pointerdown', () => { ed.seekDrag = true; });
$('ed-seek').addEventListener('pointerup', () => { ed.seekDrag = false; });
$('ed-seek').addEventListener('input', () => { if (ed.layout) edSeek($('ed-seek').value / 1000 * ed.layout.total); });
