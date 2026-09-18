// TODO: reorganize methods by category (state init, inputs, map, actions, utils)
function routeApp() {
  return {
    originInput: '', origin: '', originSuggestions: [],
    destInput:   '', dest:   '', destSuggestions:   [],
    newAddress:  '',             addressSuggestions: [],
    addresses:   [],
    fixedFirst:  null,
    fixedLast:   null,
    showAddInput:    false,
    helpOpen:        false,
    drawerOpen:      false,
    userMenuOpen:    false,
    user:            null,
    stopActionsOpen: false,
    importOpen:      false,
    importType:      'csv',
    importFile:      null,   // File object
    importStep:      'select',  // 'select' | 'preview'
    importedRows:    [],
    importedSkipped: 0,
    importError:     '',
    newDescription:  '',
    editingIndex:         null,
    editAddress:          '',
    editDescription:      '',
    editAddressSuggestions: [],
    openMenuIndex:      null,
    addressesExpanded:  false,
    showSearch:         false,
    searchQuery:        '',
    locationHint:    null,
    loading:        false,
    loadingStep:    0,
    _loadingTimers: [],
    feedbackOpen:       false,
    feedbackRating:     0,
    feedbackHoverRating: 0,
    feedbackComment:    '',
    feedbackSubmitting: false,
    feedbackDone:       false,
    contactOpen:        false,
    contactEmail:       '',
    contactMessage:     '',
    contactSubmitting:  false,
    contactDone:        false,
    contactError:       '',
    geolocating:    false,
    copied:         false,
    shared:         false,
    saved:          false,
    canNativeShare: typeof navigator !== 'undefined' && typeof navigator.share === 'function',
    result:         null,
    selectedStop:   null,
    howToVisible:   true,
    swipeHintSeen:  false,
    visitedStops:   {},
    skippedStops:   {},
    execMode:         false,
    execStopIndex:    0,
    execNavOpen:      false,
    navPreference:    null,   // 'gmaps' | 'waze' | null
    navRemember:      false,
    execMoreOpen:     false,
    execObsOpen:      false,
    execObsInput:     '',
    stopObservations: {},
    _mapInstance:   null,
    _mapMarkers:    [],
    pickLocationOpen:    false,
    pickLocationTarget:  null,   // 'origin' | 'destination' | 'stop'
    pickLocationAddress: '',
    pickLocationError:   '',
    pickLocationLoading: false,
    _pickLocationMapInstance: null,
    error:          '',
    notice:         '',
    clearConfirmOpen:   false,
    resultActionsOpen:  false,
    templatesOpen:      false,
    loginOpen:          false,
    loginMode:          'email',  // 'email' | 'sent'
    loginEmail:         '',
    loginLoading:       false,
    loginResendCooldown: 0,
    _authToken:         null,
    _pendingSave:       false,
    saveRouteNameOpen:  false,
    saveRouteName:      '',
    myRoutesOpen:       false,
    myRoutes:           [],
    myRoutesLoading:    false,
    anonOptCount:       0,
    anonOptDate:        '',
    upgradeOpen:        false,
    upgradeCpf:         '',
    upgradeBillingType: 'PIX',
    upgradeCoupon:      '',
    upgradeLoading:     false,
    get stopLimit() { return this.user?.is_exclusive ? 100 : this.user?.is_pro ? 50 : 5; },
    get anonOptsRemaining() {
      if (this.user) return Infinity;
      return Math.max(0, 5 - this.anonOptCount);
    },
    get canOptimize() {
      if (this.addresses.length < 2) return false;
      if (this.addresses.length > this.stopLimit) return false;
      if (!this.user && this.anonOptCount >= 5) return false;
      return true;
    },
    get saveRoutePlaceholder() {
      const I = window.I18N;
      return `${I.save_route_prefix}${I.days_of_week[new Date().getDay()]}`;
    },
    get visitedCount() { return Object.keys(this.visitedStops).length; },
    get execStop() { return this.result?.optimized_route?.[this.execStopIndex] ?? null; },
    get execTotal() { return this.result?.optimized_route?.length ?? 0; },

    templates: [],
    templateConfirmOpen: false,
    _pendingTemplate: null,

    init() {
      this.templates = (window.I18N.templates || []).map(t => ({ ...t, count: t.addresses.length }));
      const session = localStorage.getItem('routeSession');
      if (session) {
        try {
          const s = JSON.parse(session);
          this._authToken = s.token;
          this.user = s.user;
          // Always revalidate session to pick up server-side changes (e.g. is_pro upgrade)
          fetch('/api/v1/auth/me', { headers: { Authorization: `Bearer ${s.token}` } })
            .then(r => r.ok ? r.json() : null)
            .then(userData => {
              if (userData) {
                this.user = userData;
                localStorage.setItem('routeSession', JSON.stringify({ token: s.token, user: userData }));
              }
            })
            .catch(() => {});
        } catch (_) {}
      }

      const savedNav = localStorage.getItem('navPreference');
      if (savedNav === 'gmaps' || savedNav === 'waze') {
        this.navPreference = savedNav;
        this.navRemember   = true;
      }

      if (localStorage.getItem('howToSeen') === '1') this.howToVisible  = false;
      if (localStorage.getItem('swipeHintSeen') === '1') this.swipeHintSeen = true;

      // Load anon optimization counter (resets daily)
      const today = new Date().toISOString().slice(0, 10);
      const anonData = JSON.parse(localStorage.getItem('anonOpts') || '{}');
      this.anonOptDate  = today;
      this.anonOptCount = anonData.date === today ? (anonData.count ?? 0) : 0;

      const params       = new URLSearchParams(location.search);
      const sessionToken = params.get('session');
      const authError    = params.get('auth_error');
      const urlOrigin    = params.get('origin');
      const urlDest      = params.get('dest');
      const urlAddrs     = params.getAll('a');
      const urlSaved     = params.get('saved');

      // Handle OAuth/magic-link callback params
      if (sessionToken) {
        history.replaceState(null, '', location.pathname);
        fetch('/api/v1/auth/me', { headers: { 'Authorization': `Bearer ${sessionToken}` } })
          .then(r => r.ok ? r.json() : Promise.reject('auth/me failed'))
          .then(userData => {
            this._authToken = sessionToken;
            this.user = userData;
            localStorage.setItem('routeSession', JSON.stringify({ token: sessionToken, user: userData }));
          })
          .catch(err => {
            console.error('[auth] session callback error:', err);
          });
      } else if (authError === 'expired') {
        this.notice = window.I18N.notice_auth_link_expired;
        setTimeout(() => { this.notice = ''; }, 5000);
        history.replaceState(null, '', location.pathname);
      } else if (authError) {
        this.notice = window.I18N.notice_auth_failed;
        setTimeout(() => { this.notice = ''; }, 5000);
        history.replaceState(null, '', location.pathname);
      }

      if (params.get('upgraded') === '1') {
        history.replaceState(null, '', location.pathname);
        // Revalidate session to refresh is_pro flag
        if (this._authToken) {
          fetch('/api/v1/auth/me', { headers: { Authorization: `Bearer ${this._authToken}` } })
            .then(r => r.ok ? r.json() : null)
            .then(userData => {
              if (userData) {
                this.user = userData;
                localStorage.setItem('routeSession', JSON.stringify({ token: this._authToken, user: userData }));
              }
            });
        }
        this.notice = window.I18N.notice_welcome_pro;
        setTimeout(() => { this.notice = ''; }, 6000);
      }

      if (params.get('contact') === '1') {
        history.replaceState(null, '', location.pathname);
        this.openContact();
      }

      if (urlSaved) {
        fetch(`/api/v1/routes/saved/${urlSaved}`)
          .then(r => r.ok ? r.json() : null)
          .then(data => { if (data) this.result = data; });
        history.replaceState(null, '', location.pathname);
      } else if (params.get('expired') === '1') {
        this.notice = window.I18N.notice_link_expired;
        setTimeout(() => { this.notice = ''; }, 5000);
        history.replaceState(null, '', location.pathname);
      } else if (urlOrigin || urlDest || urlAddrs.length) {
        if (urlOrigin) { this.origin = urlOrigin; this.originInput = urlOrigin; }
        if (urlDest)   { this.dest   = urlDest;   this.destInput   = urlDest; }
        if (urlAddrs.length) this.addresses = urlAddrs.map(a => ({ address: a, description: '' }));
        history.replaceState(null, '', location.pathname);
      } else {
        const saved = localStorage.getItem('routeApp');
        if (saved) {
          try {
            const d = JSON.parse(saved);
            this.origin      = d.origin      ?? '';
            this.originInput = d.originInput ?? '';
            this.dest        = d.dest        ?? '';
            this.destInput   = d.destInput   ?? '';
            this.addresses   = (d.addresses ?? []).map(a =>
              typeof a === 'string' ? { address: a, description: '' } : a
            );
            this.fixedFirst  = d.fixedFirst  ?? null;
            this.fixedLast   = d.fixedLast   ?? null;
          } catch (_) {}
        }
      }

      const save = () => localStorage.setItem('routeApp', JSON.stringify({
        origin:      this.origin,
        originInput: this.originInput,
        dest:        this.dest,
        destInput:   this.destInput,
        addresses:   this.addresses,
        fixedFirst:  this.fixedFirst,
        fixedLast:   this.fixedLast,
      }));

      this.$watch('origin',      save);
      this.$watch('originInput', save);
      this.$watch('dest',        save);
      this.$watch('destInput',   save);
      this.$watch('addresses',   save);
      this.$watch('fixedFirst',  save);
      this.$watch('fixedLast',   save);
    },

    async fetchSuggestions(field, value) {
      if (value.trim().length < 3) {
        this[field + 'Suggestions'] = [];
        return;
      }
      try {
        const params = new URLSearchParams({ q: value });
        if (this.locationHint) {
          params.set('lat', this.locationHint.lat);
          params.set('lng', this.locationHint.lng);
        }
        const res = await fetch('/api/v1/autocomplete?' + params.toString());
        const data = await res.json();
        this[field + 'Suggestions'] = data.suggestions ?? [];
      } catch (_) {
        this[field + 'Suggestions'] = [];
      }
    },

    setOrigin() {
      const v = this.originInput.trim();
      if (v) { this.origin = v; this.originSuggestions = []; this.setLocationHint(v); }
    },
    setDest() {
      const v = this.destInput.trim();
      if (v) { this.dest = v; this.destSuggestions = []; this.setLocationHint(v); }
    },

    setLocationHint(address) {
      if (this.locationHint || !address) return;
      fetch('/api/v1/geocode?q=' + encodeURIComponent(address))
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (data) this.locationHint = { lat: data.lat, lng: data.lng }; })
        .catch(() => {});
    },

    toggleFirst(addr) {
      this.fixedFirst = this.fixedFirst === addr ? null : addr;
      if (this.fixedLast === addr) this.fixedLast = null;
    },
    toggleLast(addr) {
      this.fixedLast = this.fixedLast === addr ? null : addr;
      if (this.fixedFirst === addr) this.fixedFirst = null;
    },

    openAddInput() {
      this.showAddInput = true;
      this.$nextTick(() => { this.$refs.addInput?.focus(); });
    },

    addAddress() {
      const v = this.newAddress.trim();
      if (v) {
        this.addresses.push({ address: v, description: this.newDescription.trim() });
        this._track('stop_added', { total_stops: this.addresses.length });
        this.setLocationHint(v);
        this.newAddress = '';
        this.newDescription = '';
        this.addressSuggestions = [];
        this.showAddInput = false;
      }
    },

    streetPart(address) {
      const idx = address.indexOf(' - ');
      return idx > 0 ? address.substring(0, idx).trim() : address;
    },
    restPart(address) {
      const idx = address.indexOf(' - ');
      return idx > 0 ? address.substring(idx + 3).trim() : '';
    },

    startEdit(i) {
      this.editingIndex    = i;
      this.editAddress     = this.addresses[i].address;
      this.editDescription = this.addresses[i].description;
    },
    saveEdit(i) {
      const v = this.editAddress.trim();
      if (!v) return;
      if (this.fixedFirst === this.addresses[i].address) this.fixedFirst = v;
      if (this.fixedLast  === this.addresses[i].address) this.fixedLast  = v;
      this.addresses[i] = { address: v, description: this.editDescription.trim() };
      this.editingIndex = null;
      this.editAddressSuggestions = [];
    },
    cancelEdit() { this.editingIndex = null; this.editAddressSuggestions = []; },

    _track(eventName, params = {}) {
      if (typeof gtag === 'function') gtag('event', eventName, params);
    },

    _buildPins() {
      if (!this.result) return [];
      const pins = [];
      if (this.result.origin) pins.push({ ...this.result.origin.coordinates, type: 'origin' });
      for (const s of this.result.optimized_route)
        pins.push({ ...s.coordinates, type: 'stop', order: s.order });
      if (this.result.destination) pins.push({ ...this.result.destination.coordinates, type: 'dest' });
      return pins;
    },

    _ensureGoogleMaps() {
      if (window.google?.maps) return Promise.resolve();
      if (!this._googleMapsLoadPromise) {
        this._googleMapsLoadPromise = new Promise((resolve, reject) => {
          const s = document.createElement('script');
          s.src = `https://maps.googleapis.com/maps/api/js?key=${window.GOOGLE_MAPS_KEY}`;
          s.onload = resolve; s.onerror = reject;
          document.head.appendChild(s);
        });
      }
      return this._googleMapsLoadPromise;
    },

    async _renderMap() {
      if (!this.result) return;
      if (!window.GOOGLE_MAPS_KEY) { this._renderSvgMap(); return; }

      await this._ensureGoogleMaps();

      const pins = this._buildPins();
      if (pins.length < 2) return;

      const mapEl = document.getElementById('route-map');
      if (!mapEl) return;

      const map = new google.maps.Map(mapEl, {
        zoom: 12,
        center: { lat: pins[0].lat, lng: pins[0].lng },
        disableDefaultUI: true,
        gestureHandling: 'cooperative',
        styles: [{ featureType: 'poi', stylers: [{ visibility: 'off' }] }],
      });
      this._mapInstance = map;
      this._mapMarkers = [];

      const bounds = new google.maps.LatLngBounds();
      for (const p of pins) bounds.extend({ lat: p.lat, lng: p.lng });
      map.fitBounds(bounds, { top: 32, bottom: 32, left: 32, right: 32 });

      for (const p of pins) {
        if (p.type === 'stop') {
          const marker = new google.maps.Marker({
            position: { lat: p.lat, lng: p.lng }, map,
            label: { text: String(p.order), color: '#fff', fontSize: '11px', fontWeight: 'bold' },
            icon: {
              path: google.maps.SymbolPath.CIRCLE,
              scale: 11, fillColor: '#1d4ed8', fillOpacity: 1,
              strokeWeight: 2, strokeColor: '#fff',
            },
          });
          marker._stopOrder = p.order;
          marker.addListener('click', () => {
            const next = this.selectedStop === p.order ? null : p.order;
            this.selectedStop = next;
            this._updateMarkerIcons();
            if (next !== null) this._scrollToStop(next);
          });
          this._mapMarkers.push(marker);
        } else {
          new google.maps.Marker({
            position: { lat: p.lat, lng: p.lng }, map,
            icon: {
              path: google.maps.SymbolPath.CIRCLE,
              scale: 9,
              fillColor: p.type === 'origin' ? '#16a34a' : '#1d4ed8',
              fillOpacity: 1, strokeWeight: 2, strokeColor: '#fff',
            },
          });
        }
      }

      try {
        const res = await fetch('/api/v1/routes/polyline', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ points: pins.map(p => ({ lat: p.lat, lng: p.lng })) }),
        });
        if (res.ok) {
          const data = await res.json();
          new google.maps.Polyline({
            path: data.path.map(p => ({ lat: p.lat, lng: p.lng })),
            map, strokeColor: '#3b82f6', strokeOpacity: 0.85, strokeWeight: 3,
          });
        }
      } catch (_) {}
    },

    _renderSvgMap() {
      const pins = this._buildPins();
      if (pins.length < 2) return;
      const W = 460, H = 220, PAD = 32;
      const lats = pins.map(p => p.lat), lngs = pins.map(p => p.lng);
      const minLat = Math.min(...lats), maxLat = Math.max(...lats);
      const minLng = Math.min(...lngs), maxLng = Math.max(...lngs);
      const latRange = maxLat - minLat || 0.01;
      const lngRange = maxLng - minLng || 0.01;
      const cosLat = Math.cos((minLat + maxLat) / 2 * Math.PI / 180);
      const scaleX = (W - 2 * PAD) / (lngRange * cosLat);
      const scaleY = (H - 2 * PAD) / latRange;
      const scale  = Math.min(scaleX, scaleY);
      const offX   = (W - 2 * PAD - lngRange * cosLat * scale) / 2;
      const offY   = (H - 2 * PAD - latRange * scale) / 2;
      const project = p => ({
        x: PAD + offX + (p.lng - minLng) * cosLat * scale,
        y: H - PAD - offY - (p.lat - minLat) * scale,
      });

      const pts = pins.map(p => ({ ...p, ...project(p) }));
      const polyline = pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');

      const el = document.getElementById('route-map');
      if (!el) return;

      const markers = pts.map(p => {
        if (p.type === 'stop') {
          return `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="9" fill="#1d4ed8"/>
                  <text x="${p.x.toFixed(1)}" y="${p.y.toFixed(1)}" text-anchor="middle"
                        dominant-baseline="central" fill="white" font-size="8"
                        font-weight="bold" font-family="sans-serif">${p.order}</text>`;
        }
        const fill = p.type === 'origin' ? '#16a34a' : '#1d4ed8';
        return `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="7" fill="${fill}" stroke="white" stroke-width="2"/>`;
      }).join('');

      el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%"
          style="background:#f0f4ff;display:block" xmlns="http://www.w3.org/2000/svg">
        <polyline points="${polyline}" fill="none" stroke="#93c5fd" stroke-width="2.5"
                  stroke-dasharray="7 4" stroke-linecap="round" stroke-linejoin="round"/>
        ${markers}
      </svg>`;
    },

    _updateMarkerIcons() {
      for (const m of this._mapMarkers) {
        const active   = m._stopOrder === this.selectedStop;
        const visited  = !!this.visitedStops[m._stopOrder];
        const skipped  = !!this.skippedStops[m._stopOrder];
        const color    = visited ? '#16a34a' : skipped ? '#9ca3af' : (active ? '#1e40af' : '#1d4ed8');
        const opacity  = skipped ? 0.6 : visited ? 0.85 : 1;
        m.setIcon({
          path: google.maps.SymbolPath.CIRCLE,
          scale: active ? 13 : 11,
          fillColor: color, fillOpacity: opacity,
          strokeWeight: active ? 3 : 2, strokeColor: '#fff',
        });
        m.setLabel({
          text: visited ? '✓' : skipped ? '—' : String(m._stopOrder),
          color: '#fff',
          fontSize: active ? '12px' : '11px',
          fontWeight: 'bold',
        });
      }
    },

    toggleVisited(order, event) {
      event.stopPropagation();
      if (this.visitedStops[order]) {
        const next = { ...this.visitedStops };
        delete next[order];
        this.visitedStops = next;
      } else {
        this.visitedStops = { ...this.visitedStops, [order]: true };
        // desmarcar "pular" se estava pulado
        if (this.skippedStops[order]) {
          const next = { ...this.skippedStops };
          delete next[order];
          this.skippedStops = next;
        }
      }
      this._updateMarkerIcons();
    },

    toggleSkipped(order, event) {
      event.stopPropagation();
      if (this.skippedStops[order]) {
        const next = { ...this.skippedStops };
        delete next[order];
        this.skippedStops = next;
      } else {
        this.skippedStops = { ...this.skippedStops, [order]: true };
        // desmarcar "visitado" se estava visitado
        if (this.visitedStops[order]) {
          const next = { ...this.visitedStops };
          delete next[order];
          this.visitedStops = next;
        }
      }
      this._updateMarkerIcons();
    },

    _scrollToStop(order) {
      this.$nextTick(() => {
        const el = document.querySelector(`[data-stop="${order}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      });
    },

    stopNavUrl(stop, provider) {
      const { lat, lng } = stop.coordinates;
      if (provider === 'waze') return `https://waze.com/ul?ll=${lat},${lng}&navigate=yes`;
      return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
    },

    openExecNav() {
      if (this.navPreference) {
        // preferência salva — abre direto sem popup
        window.open(this.stopNavUrl(this.execStop, this.navPreference), '_blank');
      } else {
        this.execNavOpen = !this.execNavOpen;
      }
    },

    openNavWith(provider) {
      if (this.navRemember) {
        this.navPreference = provider;
        localStorage.setItem('navPreference', provider);
      }
      this._track(provider === 'gmaps' ? 'open_google_maps' : 'open_waze');
      window.open(this.stopNavUrl(this.execStop, provider), '_blank');
      this.execNavOpen = false;
    },

    forgetNavPreference() {
      this.navPreference = null;
      this.navRemember   = false;
      localStorage.removeItem('navPreference');
    },

    enterExecModeAt(order) {
      const idx = this.result?.optimized_route?.findIndex(s => s.order === order) ?? 0;
      this.execStopIndex = Math.max(0, idx);
      this.execNavOpen = false; this.execMoreOpen = false; this.execObsOpen = false;
      this.execMode = true;
    },

    markVisitedByOrder(order) {
      if (this.visitedStops[order]) return;
      this.visitedStops = { ...this.visitedStops, [order]: true };
      if (this.skippedStops[order]) { const n = { ...this.skippedStops }; delete n[order]; this.skippedStops = n; }
      this._updateMarkerIcons();
    },

    skipByOrder(order) {
      if (this.skippedStops[order]) return;
      this.skippedStops = { ...this.skippedStops, [order]: true };
      if (this.visitedStops[order]) { const n = { ...this.visitedStops }; delete n[order]; this.visitedStops = n; }
      this._updateMarkerIcons();
    },

    initSwipe(el, order) {
      let startX = 0, startY = 0, currentDx = 0, swipeConsumed = false;
      const THRESHOLD = 80;

      el.addEventListener('touchstart', e => {
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
        currentDx = 0; swipeConsumed = false;
        const inner = el.querySelector('.swipe-inner');
        if (inner) inner.style.transition = 'none';
      }, { passive: true });

      el.addEventListener('touchmove', e => {
        const ddx = e.touches[0].clientX - startX;
        const ddy = e.touches[0].clientY - startY;
        if (Math.abs(ddy) > Math.abs(ddx) + 8) return;
        currentDx = ddx;
        const inner = el.querySelector('.swipe-inner');
        if (inner) inner.style.transform = `translateX(${currentDx}px)`;
        el.classList.toggle('swipe-hinting-right', currentDx > 20);
        el.classList.toggle('swipe-hinting-left',  currentDx < -20);
      }, { passive: true });

      el.addEventListener('touchend', () => {
        const inner = el.querySelector('.swipe-inner');
        if (inner) { inner.style.transition = 'transform .25s ease'; inner.style.transform = 'translateX(0)'; }
        el.classList.remove('swipe-hinting-right', 'swipe-hinting-left');
        if (currentDx > THRESHOLD) { swipeConsumed = true; this.markVisitedByOrder(order); }
        else if (currentDx < -THRESHOLD) { swipeConsumed = true; this.skipByOrder(order); }
        currentDx = 0;
      });

      el.addEventListener('click', e => {
        if (swipeConsumed) { e.stopImmediatePropagation(); swipeConsumed = false; }
      }, true);
    },

    enterExecMode() {
      this.execStopIndex = 0;
      this.execNavOpen   = false;
      this.execMoreOpen  = false;
      this.execObsOpen   = false;
      this.execMode      = true;
    },
    exitExecMode() { this.execMode = false; },

    execPrev() {
      if (this.execStopIndex > 0) {
        this.execStopIndex--;
        this.execMoreOpen = false; this.execNavOpen = false; this.execObsOpen = false;
      }
    },
    execNext() {
      if (this.execStopIndex < this.execTotal - 1) {
        this.execStopIndex++;
        this.execMoreOpen = false; this.execNavOpen = false; this.execObsOpen = false;
      }
    },

    saveExecObs() {
      const stop = this.execStop;
      if (!stop) return;
      this.stopObservations = { ...this.stopObservations, [stop.order]: this.execObsInput.trim() };
      this.execObsOpen  = false;
      this.execObsInput = '';
    },

    copyStopAddress() {
      const stop = this.execStop;
      if (!stop) return;
      navigator.clipboard.writeText(stop.original_address).then(() => {
        this.notice = window.I18N.notice_addr_copied;
        setTimeout(() => { this.notice = ''; }, 2000);
      });
      this.execMoreOpen = false;
    },

    cancelAddAddress() {
      this.newAddress = '';
      this.newDescription = '';
      this.addressSuggestions = [];
      this.showAddInput = false;
    },

    getStopInfo(address) {
      return this.addresses.find(a => a.address === address) ?? { address, description: '' };
    },
    formatDuration(min) {
      if (!min) return null;
      if (min < 60) return Math.round(min) + ' min';
      const h = Math.floor(min / 60), m = Math.round(min % 60);
      return m > 0 ? `${h}h ${m}min` : `${h}h`;
    },

    async login() {
      // Magic link flow
      this.loginLoading = true;
      try {
        const res = await fetch('/api/v1/auth/magic-request', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: this.loginEmail.trim() }),
        });
        if (!res.ok) { this.error = window.I18N.err_send_link; return; }
        this.loginMode = 'sent';
        this._startResendCooldown();
      } catch (_) {
        this.error = window.I18N.err_connection;
      } finally {
        this.loginLoading = false;
      }
    },

    _startResendCooldown() {
      this.loginResendCooldown = 30;
      const tick = setInterval(() => {
        this.loginResendCooldown--;
        if (this.loginResendCooldown <= 0) clearInterval(tick);
      }, 1000);
    },

    async loginWithGoogle() {
      try {
        const res = await fetch('/api/v1/auth/google/init');
        if (!res.ok) { this.error = window.I18N.err_google_oauth; return; }
        const data = await res.json();
        window.location.href = data.redirect_url;
      } catch (_) {
        this.error = window.I18N.err_google_login;
      }
    },

    resetLogin() {
      this.loginMode = 'email';
      this.loginEmail = '';
      this.loginResendCooldown = 0;
    },

    async logout() {
      if (this._authToken) {
        fetch('/api/v1/auth/logout', {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${this._authToken}` },
        }).catch(() => {});
      }
      this._authToken = null;
      this.user = null;
      this.myRoutes = [];
      localStorage.removeItem('routeSession');
      this.userMenuOpen = false;
    },

    openSaveRouteName() {
      if (!this.result) return;
      if (!this.user) { this._pendingSave = true; this.loginOpen = true; return; }
      this.saveRouteName = '';
      this.saveRouteNameOpen = true;
      this.resultActionsOpen = false;
    },

    async loadMyRoutes() {
      if (!this.user) { this.loginOpen = true; return; }
      this.myRoutesLoading = true;
      try {
        const res = await fetch('/api/v1/routes/my-routes', {
          headers: { 'Authorization': `Bearer ${this._authToken}` },
        });
        if (!res.ok) return;
        const data = await res.json();
        this.myRoutes = data.routes;
      } catch (_) {}
      finally { this.myRoutesLoading = false; }
    },

    async deleteRoute(code) {
      try {
        const res = await fetch(`/api/v1/routes/saved/${code}`, {
          method: 'DELETE',
          headers: { 'Authorization': `Bearer ${this._authToken}` },
        });
        if (!res.ok) return;
        this.myRoutes = this.myRoutes.filter(r => r.code !== code);
        this.notice = window.I18N.notice_route_deleted;
        setTimeout(() => { this.notice = ''; }, 2500);
      } catch (_) {}
    },

    loadRoute(route) {
      const inp = route.inputs ?? {};
      this.addresses   = (inp.addresses ?? []).map(a => ({ address: a, description: '' }));
      this.origin      = inp.origin      ?? ''; this.originInput = this.origin;
      this.dest        = inp.destination ?? ''; this.destInput   = this.dest;
      this.result       = route.result ?? null;
      this.fixedFirst   = null; this.fixedLast = null;
      this.selectedStop = null;
      this.visitedStops = {};
      this.skippedStops = {};
      this.execMode     = false; this.execStopIndex = 0; this.stopObservations = {};
      this.myRoutesOpen = false;
      this._track('route_loaded', { stop_count: this.addresses.length });
      this.notice = window.I18N.notice_route_loaded.replace('{name}', route.name);
      setTimeout(() => { this.notice = ''; }, 3000);
    },

    requestLoadTemplate(t) {
      const hasContent = this.addresses.length > 0 || this.origin || this.dest;
      if (hasContent) {
        this._pendingTemplate = t;
        this.templateConfirmOpen = true;
      } else {
        this._applyTemplate(t);
      }
    },

    _applyTemplate(t) {
      this.addresses    = t.addresses.map(a => ({ address: a, description: '' }));
      this.origin       = ''; this.originInput = '';
      this.dest         = ''; this.destInput   = '';
      this.result       = null;
      this.fixedFirst   = null; this.fixedLast = null;
      this.selectedStop = null;
      this.visitedStops = {}; this.skippedStops = {}; this.stopObservations = {};
      this.execMode     = false; this.execStopIndex = 0;
      this.templatesOpen       = false;
      this.templateConfirmOpen = false;
      this._pendingTemplate    = null;
      this._saveState();
      this._track('template_loaded', { name: t.name, stop_count: t.addresses.length });
      this.notice = window.I18N.notice_template_loaded.replace('{name}', t.name);
      setTimeout(() => { this.notice = ''; }, 3000);
    },

    confirmClearAll() {
      const hasContent = this.addresses.length > 0 || this.origin || this.dest || this.result;
      if (!hasContent) { this.clearAll(); return; }
      this.clearConfirmOpen = true;
    },

    async saveAndClear() {
      await this.saveRoute();
      this.clearConfirmOpen = false;
      this.clearAll();
    },

    clearAll() {
      this.origin = ''; this.originInput = ''; this.originSuggestions = [];
      this.dest   = ''; this.destInput   = ''; this.destSuggestions   = [];
      this.newAddress = ''; this.addressSuggestions = [];
      this.addresses = [];
      this.fixedFirst = null;
      this.fixedLast  = null;
      this.showAddInput = false;
      this.newDescription = '';
      this.locationHint = null;
      this.result = null;
      this.selectedStop = null;
      this.visitedStops = {};
      this.skippedStops = {};
      this.execMode     = false; this.execStopIndex = 0; this.stopObservations = {};
      this._mapInstance = null;
      this._mapMarkers = [];
      this.error  = '';
      localStorage.removeItem('routeApp');
    },

    async geolocate() {
      if (!navigator.geolocation) {
        this.error = window.I18N.err_geo_unsupported;
        return;
      }
      this.geolocating = true;
      this.error = '';
      navigator.geolocation.getCurrentPosition(
        async (pos) => {
          try {
            const { latitude: lat, longitude: lng } = pos.coords;
            this.locationHint = { lat, lng };
            const res = await fetch(`/api/v1/reverse?lat=${lat}&lng=${lng}`);
            if (!res.ok) throw new Error();
            const data = await res.json();
            this.originInput = data.address;
            this.origin = data.address;
            this.originSuggestions = [];
          } catch (_) {
            this.error = window.I18N.err_geo_address;
          } finally {
            this.geolocating = false;
          }
        },
        () => {
          this.error = window.I18N.err_geo_denied;
          this.geolocating = false;
        }
      );
    },

    async openPickLocation(target) {
      if (!window.GOOGLE_MAPS_KEY) return;
      this.pickLocationTarget = target;
      this.pickLocationAddress = '';
      this.pickLocationError = '';
      this.pickLocationLoading = false;
      this.pickLocationOpen = true;

      await this._ensureGoogleMaps();
      await this.$nextTick();

      const mapEl = document.getElementById('pick-location-map');
      if (!mapEl) return;

      const center = this.locationHint || { lat: -14.235, lng: -51.9253 };
      const map = new google.maps.Map(mapEl, {
        zoom: this.locationHint ? 16 : 4,
        center,
        disableDefaultUI: true,
        gestureHandling: 'greedy',
        styles: [{ featureType: 'poi', stylers: [{ visibility: 'off' }] }],
      });
      this._pickLocationMapInstance = map;

      map.addListener('idle', () => this._reverseGeocodeMapCenter());
    },

    async _reverseGeocodeMapCenter() {
      const map = this._pickLocationMapInstance;
      if (!map) return;
      const c = map.getCenter();
      const lat = c.lat(), lng = c.lng();
      this.pickLocationLoading = true;
      this.pickLocationError = '';
      try {
        const res = await fetch(`/api/v1/reverse?lat=${lat}&lng=${lng}`);
        if (!res.ok) throw new Error();
        const data = await res.json();
        this.pickLocationAddress = data.address;
      } catch (_) {
        this.pickLocationAddress = '';
        this.pickLocationError = window.I18N.pick_location_error;
      } finally {
        this.pickLocationLoading = false;
      }
    },

    closePickLocation() {
      this.pickLocationOpen = false;
      this.pickLocationTarget = null;
      this.pickLocationAddress = '';
      this.pickLocationError = '';
      this._pickLocationMapInstance = null;
    },

    confirmPickLocation() {
      const address = this.pickLocationAddress;
      if (!address) return;
      if (this.pickLocationTarget === 'origin') {
        this.originInput = address; this.origin = address; this.originSuggestions = [];
      } else if (this.pickLocationTarget === 'destination') {
        this.destInput = address; this.dest = address; this.destSuggestions = [];
      } else if (this.pickLocationTarget === 'stop') {
        this.newAddress = address; this.addressSuggestions = [];
      }
      this.closePickLocation();
    },

    async shareRoute() {
      let shareUrl = location.origin;
      try {
        const res = await fetch('/api/v1/shorten', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            addresses: this.addresses.map(a => a.address),
            ...(this.origin && { origin: this.origin }),
            ...(this.dest   && { destination: this.dest }),
          }),
        });
        if (res.ok) {
          const data = await res.json();
          shareUrl = `${location.origin}${data.path}`;
        }
      } catch (_) {}

      const msg = (window.I18N?.share_msg) || 'Veja os endereços que separei para você — clique em otimizar para encontrar a melhor rota!';
      const shareData = { title: window.I18N?.share_title || 'Rota — Rota Otimizada', text: msg, url: shareUrl };
      if (navigator.share && navigator.canShare?.(shareData)) {
        navigator.share(shareData).catch(() => {});
      } else {
        navigator.clipboard.writeText(`${msg}\n${shareUrl}`).then(() => {
          this.shared = true;
          setTimeout(() => { this.shared = false; }, 2000);
        });
      }
    },

    copyRoute() {
      if (!this.result) return;
      const I = window.I18N;
      const lines = [I.copy_route_header.replace('{distance}', this.result.total_distance_km), ''];
      if (this.result.origin) lines.push(`0. ${this.result.origin.address} ${I.copy_route_origin_label}`);
      for (const stop of this.result.optimized_route) {
        const item = this.addresses.find(a => a.address === stop.original_address);
        const label = item?.description ? `${item.description} — ` : '';
        lines.push(`${stop.order}. ${label}${stop.original_address}`);
      }
      if (this.result.destination) lines.push(`F. ${this.result.destination.address} ${I.copy_route_dest_label}`);
      lines.push('', window.I18N?.copy_signature || 'Rota otimizada com rotaotimizada.com.br');
      navigator.clipboard.writeText(lines.join('\n')).then(() => {
        this.copied = true;
        setTimeout(() => { this.copied = false; }, 2000);
      });
    },

    async saveRoute() {
      if (!this.result || !this.user) return;
      try {
        const res = await fetch('/api/v1/routes/save', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${this._authToken}`,
          },
          body: JSON.stringify({
            name: this.saveRouteName.trim() || 'Rota sem nome',
            result: this.result,
            inputs: {
              addresses: this.addresses.map(a => a.address),
              ...(this.origin && { origin: this.origin }),
              ...(this.dest   && { destination: this.dest }),
            },
          }),
        });
        if (!res.ok) {
          if (res.status === 401) { this.user = null; this._authToken = null; localStorage.removeItem('routeSession'); }
          return;
        }
        this.saveRouteNameOpen = false;
        this.saveRouteName = '';
        this.saved = true;
        setTimeout(() => { this.saved = false; }, 2000);
      } catch (_) {}
    },

    _csvEscape(value) {
      const s = String(value ?? '');
      return (s.includes(',') || s.includes('"') || s.includes('\n'))
        ? '"' + s.replace(/"/g, '""') + '"'
        : s;
    },

    _triggerDownload(content, filename, mime) {
      const blob = new Blob([content], { type: mime });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },

    _parseCSVText(text) {
      const clean = text.replace(/^﻿/, '');
      const lines = clean.split(/\r?\n/);
      if (!lines.length || !lines[0].trim()) return { error: window.I18N.err_empty_file };

      const first = lines[0];
      const semi  = (first.match(/;/g) || []).length;
      const comma = (first.match(/,/g) || []).length;
      const delim = semi > comma ? ';' : ',';

      const parseRow = (line) => {
        const fields = [];
        let cur = '', inQuote = false;
        for (let i = 0; i < line.length; i++) {
          const ch = line[i];
          if (inQuote) {
            if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
            else if (ch === '"') { inQuote = false; }
            else { cur += ch; }
          } else {
            if (ch === '"') { inQuote = true; }
            else if (ch === delim) { fields.push(cur.trim()); cur = ''; }
            else { cur += ch; }
          }
        }
        fields.push(cur.trim());
        return fields;
      };

      const headers = parseRow(lines[0]).map(h => h.toLowerCase().replace(/\s+/g, ''));
      const I = window.I18N;
      const _findCol = (primary, fallback) => {
        const i = headers.indexOf(primary);
        return i !== -1 ? i : headers.indexOf(fallback);
      };
      const endIdx  = _findCol(I.import_col_address, I.import_col_address === 'address' ? 'endereco' : 'address');
      const descIdx = _findCol(I.import_col_desc, I.import_col_desc === 'description' ? 'descricao' : 'description');

      if (endIdx === -1) return { error: I.err_col_not_found };

      let rows = [], skipped = 0;
      for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        const fields = parseRow(lines[i]);
        const address = (fields[endIdx] ?? '').trim();
        if (!address) { skipped++; continue; }
        rows.push({ address, description: descIdx >= 0 ? (fields[descIdx] ?? '').trim() : '' });
      }
      return { rows, skipped };
    },

    async _parseXLSXFile(file) {
      if (!window.XLSX) {
        await new Promise((resolve, reject) => {
          const s = document.createElement('script');
          s.src = 'https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js';
          s.onload = resolve; s.onerror = reject;
          document.head.appendChild(s);
        });
      }
      const buf = await file.arrayBuffer();
      const wb  = window.XLSX.read(buf, { type: 'array' });
      const ws  = wb.Sheets[wb.SheetNames[0]];
      const raw = window.XLSX.utils.sheet_to_json(ws, { raw: false, defval: '' });
      if (!raw.length) return { error: window.I18N.err_empty_file };

      const normalize = (k) => k.toLowerCase().replace(/\s+/g, '');
      const I = window.I18N;
      const _findKey = (primary, fallback) =>
        Object.keys(raw[0]).find(k => normalize(k) === primary) ||
        Object.keys(raw[0]).find(k => normalize(k) === fallback);
      const endKey  = _findKey(I.import_col_address, I.import_col_address === 'address' ? 'endereco' : 'address');
      const descKey = _findKey(I.import_col_desc, I.import_col_desc === 'description' ? 'descricao' : 'description');

      if (!endKey) return { error: I.err_col_not_found };

      let rows = [], skipped = 0;
      for (const obj of raw) {
        const address = (obj[endKey] ?? '').trim();
        if (!address) { skipped++; continue; }
        rows.push({ address, description: descKey ? (obj[descKey] ?? '').trim() : '' });
      }
      return { rows, skipped };
    },

    async importAddresses() {
      if (!this.importFile) return;
      this.importError = '';
      let parsed;
      try {
        if (this.importType === 'csv') {
          const text = await this.importFile.text();
          parsed = this._parseCSVText(text);
        } else {
          parsed = await this._parseXLSXFile(this.importFile);
        }
      } catch (_) {
        this.importError = window.I18N.err_read_file;
        return;
      }
      if (parsed.error) { this.importError = parsed.error; return; }
      if (!parsed.rows.length) { this.importError = window.I18N.err_no_valid_address; return; }
      this.importedRows    = parsed.rows;
      this.importedSkipped = parsed.skipped;
      this.importStep      = 'preview';
    },

    applyImport(mode) {
      const incoming = this.importedRows;
      const current  = mode === 'replace' ? [] : this.addresses;
      const combined = [...current, ...incoming];
      // TODO: enforce free plan limit (5 stops) — block here when user.isPro is false
      if (combined.length > 50) {
        const I = window.I18N;
        this.importError = I.import_limit_exceeded.replace('{limit}', 50).replace('{total}', combined.length);
        return;
      }
      this.addresses   = combined;
      this._track('csv_imported', { count: incoming.length, mode });
      const count      = incoming.length;
      const I          = window.I18N;
      const tpl        = mode === 'replace' ? I.notice_import_replaced : I.notice_import_added;
      this.notice      = tpl.replace('{count}', count);
      setTimeout(() => { this.notice = ''; }, 3000);
      this.importOpen    = false;
      this.importFile    = null;
      this.importStep    = 'select';
      this.importedRows  = [];
      this.importedSkipped = 0;
      this.importError   = '';
    },

    downloadTemplate() {
      const I = window.I18N;
      const colAddr = I.import_col_address, colDesc = I.import_col_desc;
      const content = [
        `${colAddr},${colDesc}`,
        `${this._csvEscape(I.import_template_ex1_addr)},${this._csvEscape(I.import_template_ex1_desc)}`,
        `${this._csvEscape(I.import_template_ex2_addr)},""`,
      ].join('\r\n');
      this._triggerDownload('﻿' + content, I.import_template_filename, 'text/csv;charset=utf-8;');
    },

    exportAddresses() {
      const I = window.I18N;
      if (!this.addresses.length) { this.notice = I.export_no_stops; setTimeout(() => { this.notice = ''; }, 2500); return; }
      const lines = [`${I.import_col_address},${I.import_col_desc}`];
      for (const a of this.addresses) lines.push(`${this._csvEscape(a.address)},${this._csvEscape(a.description)}`);
      this._track('csv_exported', { stop_count: this.addresses.length });
      this._triggerDownload('﻿' + lines.join('\r\n'), I.export_addresses_filename, 'text/csv;charset=utf-8;');
    },

    exportRouteCSV() {
      if (!this.result) return;
      const I = window.I18N;
      const header = [I.export_col_order, I.export_col_address, I.export_col_lat, I.export_col_lng, I.export_col_distance, I.export_col_duration];
      const rows = [header.map(c => this._csvEscape(c)).join(',')];

      if (this.result.origin) {
        rows.push([this._csvEscape(I.timeline_start), this._csvEscape(this.result.origin.address),
          this.result.origin.coordinates.lat, this.result.origin.coordinates.lng, '', ''].join(','));
      }
      for (const s of this.result.optimized_route) {
        rows.push([s.order, this._csvEscape(s.original_address), s.coordinates.lat, s.coordinates.lng,
          s.leg_distance_km != null ? s.leg_distance_km.toFixed(2) : '',
          s.leg_duration_min != null ? Math.round(s.leg_duration_min) : ''].join(','));
      }
      if (this.result.destination) {
        rows.push([this._csvEscape(I.timeline_end), this._csvEscape(this.result.destination.address),
          this.result.destination.coordinates.lat, this.result.destination.coordinates.lng, '', ''].join(','));
      }

      this._triggerDownload('﻿' + rows.join('\r\n'), I.export_csv_filename, 'text/csv;charset=utf-8;');
      this._track('route_exported_csv', { stops: this.result.optimized_route.length });
    },

    exportRoutePDF() {
      if (!this.result) return;
      const I = window.I18N;
      const brand = document.querySelector('title')?.textContent?.split('–')[0]?.trim() || 'Rota Otimizada';
      const date = new Date().toLocaleDateString();
      const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

      let rows = '';
      const addRow = (label, address, dist, dur, special) => {
        const distCell = dist != null ? dist.toFixed(2) + ' km' : '—';
        const durCell  = dur  != null ? Math.round(dur) + ' min' : '—';
        rows += `<tr${special ? ' class="sp"' : ''}><td>${esc(String(label))}</td><td>${esc(address)}</td><td>${distCell}</td><td>${durCell}</td></tr>`;
      };

      if (this.result.origin) addRow('●', this.result.origin.address, null, null, true);
      for (const s of this.result.optimized_route) addRow(s.order, s.original_address, s.leg_distance_km, s.leg_duration_min, false);
      if (this.result.destination) addRow('○', this.result.destination.address, null, null, true);

      const totalDist = this.result.total_distance_km ? this.result.total_distance_km.toFixed(1) + ' km' : '';
      const totalTime = this.result.total_duration_min ? Math.round(this.result.total_duration_min) + ' min' : '';
      const statLine = [
        totalDist ? `${esc(I.export_pdf_total_dist)}: <strong>${totalDist}</strong>` : '',
        totalTime ? `${esc(I.export_pdf_total_time)}: <strong>${totalTime}</strong>` : ''
      ].filter(Boolean).join(' &nbsp;·&nbsp; ');

      const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${esc(brand)}</title>
<style>
body{font-family:Arial,sans-serif;font-size:12px;margin:15mm 12mm;color:#1a1a2e}
h1{font-size:16px;margin:0 0 4px}
.meta{color:#555;font-size:11px;margin-bottom:14px}
table{width:100%;border-collapse:collapse}
th{background:#1a1a2e;color:#fff;text-align:left;padding:5px 8px;font-size:11px}
td{padding:4px 8px;border-bottom:1px solid #eee}
tr:nth-child(even) td{background:#f9f9f9}
tr.sp td{font-weight:bold;background:#eef2ff}
.footer{margin-top:14px;font-size:10px;color:#999;text-align:right}
@media print{body{margin:8mm 8mm}}
</style></head>
<body>
<h1>${esc(brand)}</h1>
<div class="meta">${statLine}${statLine ? ' &nbsp;&nbsp; ' : ''}${date}</div>
<table>
<thead><tr><th>#</th><th>${esc(I.export_col_address)}</th><th>${esc(I.export_col_distance)}</th><th>${esc(I.export_col_duration)}</th></tr></thead>
<tbody>${rows}</tbody>
</table>
<div class="footer">${esc(I.export_pdf_generated)} ${esc(brand)}</div>
<script>window.onload=()=>window.print();<\/script>
</body></html>`;

      const w = window.open('', '_blank');
      if (w) { w.document.write(html); w.document.close(); }
      this._track('route_exported_pdf', { stops: this.result.optimized_route.length });
    },

    _startLoadingSteps() {
      this.loadingStep = 1;
      this._loadingTimers = [
        setTimeout(() => { this.loadingStep = 2; }, 1500),
        setTimeout(() => { this.loadingStep = 3; }, 3500),
      ];
    },

    _stopLoadingSteps(done = false) {
      this._loadingTimers.forEach(t => clearTimeout(t));
      this._loadingTimers = [];
      if (done) {
        this.loadingStep = 4;
        setTimeout(() => { this.loadingStep = 0; }, 1200);
      } else {
        this.loadingStep = 0;
      }
    },

    _maybeShowFeedback() {
      if (localStorage.getItem('feedbackDismissed') === 'true') return;
      const count = (parseInt(localStorage.getItem('optSuccessCount') || '0', 10)) + 1;
      localStorage.setItem('optSuccessCount', String(count));
      if (count === 2) {
        setTimeout(() => { this.feedbackOpen = true; }, 2000);
      }
    },

    async submitFeedback() {
      if (this.feedbackRating < 1) return;
      this.feedbackSubmitting = true;
      try {
        const headers = { 'Content-Type': 'application/json' };
        if (this._authToken) headers['Authorization'] = `Bearer ${this._authToken}`;
        await fetch('/api/v1/feedback', {
          method:  'POST',
          headers,
          body: JSON.stringify({
            rating:  this.feedbackRating,
            comment: this.feedbackComment.trim() || null,
          }),
        });
        this._track('feedback_submitted', { rating: this.feedbackRating });
      } catch (e) {
        // fire-and-forget: don't block the thank-you UI on network errors
      } finally {
        this.feedbackSubmitting = false;
        this.feedbackDone = true;
        localStorage.setItem('feedbackDismissed', 'true');
        setTimeout(() => { this.feedbackOpen = false; }, 1500);
      }
    },

    closeFeedback() {
      this.feedbackOpen = false;
    },

    dismissFeedback() {
      localStorage.setItem('feedbackDismissed', 'true');
      this.feedbackOpen = false;
    },

    openContact() {
      if (this.user) this.contactEmail = this.user.email;
      this.contactOpen = true;
    },

    closeContact() {
      this.contactOpen = false;
    },

    async submitContact() {
      if (!this.contactEmail.trim() || !this.contactMessage.trim()) return;
      this.contactSubmitting = true;
      this.contactError = '';
      try {
        const headers = { 'Content-Type': 'application/json' };
        if (this._authToken) headers['Authorization'] = `Bearer ${this._authToken}`;
        const res = await fetch('/api/v1/contact', {
          method:  'POST',
          headers,
          body: JSON.stringify({
            email:   this.contactEmail.trim(),
            message: this.contactMessage.trim(),
          }),
        });
        if (!res.ok) throw new Error('contact request failed');
        this._track('contact_submitted', {});
        this.contactDone = true;
        setTimeout(() => {
          this.contactOpen = false;
          this.contactDone = false;
          this.contactMessage = '';
        }, 1800);
      } catch (e) {
        this.contactError = window.I18N.err_api_connection;
      } finally {
        this.contactSubmitting = false;
      }
    },

    async optimize() {
      this.error  = '';
      this.result = null;
      this.loading = true;
      this._startLoadingSteps();

      const body = { addresses: this.addresses.map(a => a.address) };
      if (this.origin)     body.origin      = this.origin;
      if (this.dest)       body.destination = this.dest;
      if (this.fixedFirst) body.fixed_first = this.fixedFirst;
      if (this.fixedLast)  body.fixed_last  = this.fixedLast;

      this._track('route_optimization_started', { stop_count: this.addresses.length });

      try {
        const headers = { 'Content-Type': 'application/json' };
        if (this._authToken) headers['Authorization'] = `Bearer ${this._authToken}`;
        const res = await fetch('/api/v1/routes/optimize', {
          method:  'POST',
          headers,
          body:    JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) {
          // Show user-friendly message for plan limit errors
          if (res.status === 403 && data.detail?.code === 'STOP_LIMIT_EXCEEDED') {
            const tpl = this.user ? window.I18N.err_stop_limit_pro : window.I18N.err_stop_limit_anon;
            this.error = tpl.replace('{limit}', data.detail.limit);
          } else {
            this.error = typeof data.detail === 'string' ? data.detail : `Erro ${res.status}`;
          }
        } else {
          // Increment anon daily optimization counter
          if (!this.user) {
            this.anonOptCount++;
            localStorage.setItem('anonOpts', JSON.stringify({ date: this.anonOptDate, count: this.anonOptCount }));
          }
          this.result = data;
          this._track('route_optimization_success', {
            stops: data.optimized_route?.length ?? 0,
            distance_km: data.total_distance_km ?? 0,
          });
          this.$nextTick(() => { this._renderMap(); });
          this.$nextTick(() => {
            const el = document.querySelector('.result-section');
            if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.pageYOffset - 16, behavior: 'smooth' });
          });
          this._maybeShowFeedback();
        }
      } catch (e) {
        this.error = window.I18N.err_api_connection;
      } finally {
        this.loading = false;
        this._stopLoadingSteps(!!this.result);
      }
    },

    formatCpfCnpj(value) {
      const digits = value.replace(/\D/g, '').slice(0, 14);
      if (digits.length <= 11) {
        return digits
          .replace(/(\d{3})(\d)/, '$1.$2')
          .replace(/(\d{3})(\d)/, '$1.$2')
          .replace(/(\d{3})(\d{1,2})$/, '$1-$2');
      }
      return digits
        .replace(/(\d{2})(\d)/, '$1.$2')
        .replace(/(\d{3})(\d)/, '$1.$2')
        .replace(/(\d{3})(\d)/, '$1/$2')
        .replace(/(\d{4})(\d{1,2})$/, '$1-$2');
    },

    async startCheckout() {
      if (!this.user) { this.loginOpen = true; return; }
      this.upgradeLoading = true;
      this.error = '';

      if (window.I18N.show_cpf_field) {
        // PT: Asaas flow — CPF/CNPJ required
        const cpf = this.upgradeCpf.replace(/\D/g, '');
        if (cpf.length !== 11 && cpf.length !== 14) {
          this.error = 'Informe um CPF (11 dígitos) ou CNPJ (14 dígitos) válido.';
          this.upgradeLoading = false;
          return;
        }
        try {
          const res = await fetch('/api/v1/billing/checkout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this._authToken}` },
            body: JSON.stringify({
              cpf_cnpj: cpf,
              billing_type: this.upgradeBillingType,
              coupon_code: this.upgradeCoupon.trim() || undefined,
            }),
          });
          const data = await res.json();
          if (res.ok && data.coupon_applied) {
            this.upgradeOpen = false;
            let userData = null;
            const res2 = await fetch('/api/v1/auth/me', { headers: { Authorization: `Bearer ${this._authToken}` } });
            if (res2.ok) {
              userData = await res2.json();
              this.user = userData;
              localStorage.setItem('routeSession', JSON.stringify({ token: this._authToken, user: userData }));
            }
            this.notice = userData?.is_exclusive ? window.I18N.notice_welcome_exclusive : window.I18N.notice_welcome_pro;
            setTimeout(() => { this.notice = ''; }, 6000);
          } else if (res.ok && data.payment_url) {
            this.upgradeOpen = false;
            window.open(data.payment_url, '_blank');
          } else {
            this.error = typeof data.detail === 'string' ? data.detail : 'Erro ao iniciar pagamento.';
          }
        } catch (e) {
          this.error = window.I18N.err_connection;
        } finally {
          this.upgradeLoading = false;
        }
        return;
      }

      // EN: Stripe Checkout — redirect to hosted Stripe page
      try {
        const res = await fetch('/api/v1/billing/stripe/checkout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${this._authToken}` },
        });
        const data = await res.json();
        if (res.ok && data.payment_url) {
          window.location.href = data.payment_url;
        } else {
          this.error = typeof data.detail === 'string' ? data.detail : window.I18N.err_connection;
        }
      } catch (e) {
        this.error = window.I18N.err_connection;
      } finally {
        this.upgradeLoading = false;
      }
    },
  };
}
