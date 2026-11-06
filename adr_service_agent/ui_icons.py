"""Small inline outline icons for the local dashboard."""

PATHS = {
    'ask': '<path d="M21 11a8 8 0 0 1-8 8H7l-5 3 2-6a8 8 0 0 1-1-5 9 9 0 0 1 18 0z"/><path d="M8 10h8M8 14h5"/>',
    'home': '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    'power': '<path d="M12 3v8M6.3 5.8a9 9 0 1 0 11.4 0"/>',
    'energy': '<path d="m13 2-8 12h6l-1 8 9-13h-6z"/>',
    'network': '<rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 17.5h.01M11 17.5h.01M12 14v-2M9 11a5 5 0 0 1 6 0M6 8a9 9 0 0 1 12 0M3 5a14 14 0 0 1 18 0"/>',
    'wireless': '<path d="M3 7a15 15 0 0 1 18 0M6 11a10 10 0 0 1 12 0M9 15a5 5 0 0 1 6 0M12 19h.01"/>',
    'av': '<rect x="3" y="3" width="18" height="14" rx="2"/><path d="M8 21h8M12 17v4m-2-11 5 3-5 3z"/>',
    'audio': '<path d="m11 4-6 5H2v6h3l6 5zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>',
    'processor': '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
    'switch': '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 10h2m3 0h2m3 0h2M6 14h2m3 0h2m3 0h2"/>',
    'settings': '<path d="M19.39,8.94 L21.78,9.92 L21.78,14.08 L19.39,15.06 L19.39,15.06 L20.39,17.45 L17.45,20.39 L15.06,19.39 L15.06,19.39 L14.08,21.78 L9.92,21.78 L8.94,19.39 L8.94,19.39 L6.55,20.39 L3.61,17.45 L4.61,15.06 L4.61,15.06 L2.22,14.08 L2.22,9.92 L4.61,8.94 L4.61,8.94 L3.61,6.55 L6.55,3.61 L8.94,4.61 L8.94,4.61 L9.92,2.22 L14.08,2.22 L15.06,4.61 L15.06,4.61 L17.45,3.61 L20.39,6.55 L19.39,8.94 Z"/><circle cx="12" cy="12" r="3.2"/>',
    'history': '<path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v6l4 2"/>',
    'chevron': '<path d="m9 5 7 7-7 7"/>',
}


def icon(name):
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + PATHS.get(name, PATHS['home']) + '</svg>'

