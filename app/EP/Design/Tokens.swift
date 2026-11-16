import AppKit
import SwiftUI

// Design tokens from plans/frontend-design.md §2, in one place. Views use these, never
// literal colours, sizes or fonts. Hex values are the design's CSS custom properties
// (light / dark); selection and accent follow the system accent colour (plan decision 2),
// with the design's blue as what the default accent looks like.

// MARK: - Colours (§2.1)

enum Palette {
    /// `--bg`: content background, cards, buttons.
    static let bg = Color(light: 0xFFFFFF, dark: 0x1E1E20)
    /// `--side`: artist/show column, sheet footers, side panes.
    static let side = Color(light: 0xF6F6F8, dark: 0x252527)
    /// `--bar`: toolbar, player bar, settings title bar.
    static let bar = Color(light: 0xECECEF, dark: 0x2C2C2F)
    /// `--text`: primary text; play button fill.
    static let text = Color(nsColor: NSPalette.text)
    /// `--text2`: secondary text, scrubber progress and marks.
    static let text2 = Color(nsColor: NSPalette.text2)
    /// `--text3`: tertiary text, section labels, numbers, icons, dimmed titles.
    static let text3 = Color(nsColor: NSPalette.text3)
    /// `--line`: 1 pt separators and control borders, scrubber track.
    static let line = Color(nsColor: NSPalette.line)
    /// `--sel`: focused selection, primary buttons, focus borders (design: #0062cc / #0a5bc4).
    static let sel = Color(nsColor: .selectedContentBackgroundColor)
    /// `--seltext`: text on `sel`.
    static let selText = Color.white
    /// `--selsoft`: unfocused selection, chosen option, ⌘-hover link background
    /// (design: #dce7f7 / #33405a): the accent blended into the background.
    static let selSoft = Color(nsColor: NSPalette.selSoft)
    /// `--accent`: links, text caret, progress, drop outline (design: #0062cc / #64adff).
    static let accent = Color.accentColor
    /// `--warn`: radio excerpt, review badge, "needs decision".
    static let warn = Color(light: 0x935300, dark: 0xF2A649)
    /// `--danger`: broken link, file missing, failed, server unreachable.
    static let danger = Color(nsColor: NSPalette.danger)
    /// `--field`: text field background.
    static let field = Color(light: 0xFFFFFF, dark: 0x1A1A1C)
    /// `--chip`: segmented track, kind badges, tag chips, banners, codec chips.
    static let chip = Color(nsColor: NSPalette.chip)
    /// `--skel`: loading skeleton bars.
    static let skel = Color(light: 0xECECF0, dark: 0x2E2E31)
    /// `--ok`: "Connected" in Settings.
    static let ok = Color(light: 0x1D7A3A, dark: 0x5FD187)
    /// Badge text on `warn` / `danger`.
    static let badgeText = Color.white
    /// Cover thumbnails' inner hairline (`inset 0 0 0 1px rgba(0,0,0,.08)`).
    static let coverHairline = Color.black.opacity(0.08)
}

/// The colours AppKit views draw with (the note editor's text view): the same tokens as
/// `NSColor`s, appearance-dependent.
enum NSPalette {
    static let text = NSColor.dynamic(light: 0x1D1D1F, dark: 0xF2F2F4)
    static let text2 = NSColor.dynamic(light: 0x55555A, dark: 0xC0C0C6)
    static let text3 = NSColor.dynamic(light: 0x6B6B70, dark: 0xA0A0A7)
    static let line = NSColor.dynamic(light: 0xDCDCE0, dark: 0x3B3B3F)
    static let chip = NSColor.dynamic(light: 0xEBEBEF, dark: 0x36363A)
    static let danger = NSColor.dynamic(light: 0xBF2619, dark: 0xFF8478)
    static let selSoft = NSColor.accentBlend(light: 0xFFFFFF, dark: 0x1E1E20, fraction: (0.15, 0.3))
    static let accent = NSColor.controlAccentColor
}

/// `--shadow`: popovers, palettes, toasts.
struct PopoverShadow: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme

    func body(content: Content) -> some View {
        let dark = colorScheme == .dark
        content
            .shadow(color: .black.opacity(dark ? 0.5 : 0.18), radius: 20, y: 12)
            .overlay(
                RoundedRectangle(cornerRadius: Radius.card)
                    .strokeBorder(dark ? Color.white.opacity(0.1) : Color.black.opacity(0.08))
            )
    }
}

// MARK: - Typography (§2.2)

enum Typography {
    /// 13 regular: body (macOS body size).
    static let body = Font.system(size: 13)
    /// 13 medium: row titles (release groups, shows, episodes).
    static let rowTitle = Font.system(size: 13, weight: .medium)
    /// 13 semibold: names, column headers.
    static let name = Font.system(size: 13, weight: .semibold)
    /// 12 regular: secondary text, column header meta, small buttons, hints.
    static let secondary = Font.system(size: 12)
    /// 12 semibold: "Notes that link here · 3".
    static let subheading = Font.system(size: 12, weight: .semibold)
    /// 11 regular: row meta, player times, key hints, pills, chips.
    static let meta = Font.system(size: 11)
    /// 11 semibold uppercase, tracking `sectionLabelTracking`: NOTE, TAGS · 3, DISC 2.
    static let sectionLabel = Font.system(size: 11, weight: .semibold)
    static let sectionLabelTracking: CGFloat = 0.55
    /// 10 bold uppercase, tracking `badgeTracking`: ARTIST, ALBUM, TRACK, EPISODE.
    static let badge = Font.system(size: 10, weight: .bold)
    static let badgeTracking: CGFloat = 0.6
    /// 14 regular, line height 1.55: note body.
    static let noteBody = Font.system(size: 14)
    static let noteLineSpacing: CGFloat = 14 * 0.55
    /// 14 semibold: panel titles in the note editor and radio detail, popover titles.
    static let panelTitle = Font.system(size: 14, weight: .semibold)
    /// Note headings (plan decision 4).
    static let heading1 = Font.system(size: 22, weight: .semibold)
    static let heading2 = Font.system(size: 18, weight: .semibold)
    static let heading3 = Font.system(size: 15, weight: .semibold)
    /// The note editor's sizes (AppKit): body 14, headings 22 / 18 / 15, code 12.5.
    static let noteSizes = (body: 14.0, headings: [22.0, 18.0, 15.0], code: 12.5)
    /// 15 semibold: import review title.
    static let reviewTitle = Font.system(size: 15, weight: .semibold)
    /// 16 semibold: "Import" sheet heading.
    static let sheetHeading = Font.system(size: 16, weight: .semibold)
    /// 17: search palette input (regular), empty-state heading (semibold).
    static let paletteInput = Font.system(size: 17)
    static let emptyHeading = Font.system(size: 17, weight: .semibold)
    /// 18 semibold: review question.
    static let question = Font.system(size: 18, weight: .semibold)
    /// SF Mono 11: paths, URLs, MBIDs, logs, server address. Numbers use `.monospacedDigit()`.
    static let mono = Font.system(size: 11, design: .monospaced)
}

// MARK: - Spacing, radii, sizes (§2.3)

enum Radius {
    /// List rows (artist, track).
    static let row: CGFloat = 5
    /// Larger list rows (release group, show, episode, hits), small buttons.
    static let rowLarge: CGFloat = 6
    static let button: CGFloat = 6
    /// Large import buttons and link field.
    static let largeButton: CGFloat = 8
    /// Cards, panels, popovers.
    static let card: CGFloat = 10
    static let sheet: CGFloat = 12
    /// Codec chips, kind badges, key caps.
    static let chip: CGFloat = 4
    /// Artist filter field.
    static let filterField: CGFloat = 6
    /// Tag chips (height 26) and pills: fully round.
    static let tagChip: CGFloat = 13
    static let pill: CGFloat = 10
    /// Segmented control track and search field.
    static let segmentedTrack: CGFloat = 7
    static let segment: CGFloat = 5
    static let searchField: CGFloat = 7
    /// Cover thumbnails: 32 pt / 36–40 pt. Artist, show and episode images are circles.
    static let coverSmall: CGFloat = 3
    static let cover: CGFloat = 4
}

enum RowHeight {
    static let artist: CGFloat = 22
    static let releaseGroup: CGFloat = 42
    static let track: CGFloat = 26
    static let discHeading: CGFloat = 24
    static let show: CGFloat = 40
    static let episode: CGFloat = 48
    static let tracklistEntry: CGFloat = 26
    static let searchHit: CGFloat = 46
    static let autocompleteAction: CGFloat = 36
    static let tagSuggestion: CGFloat = 28
    static let tagChip: CGFloat = 26
    static let jobMin: CGFloat = 48
    static let watchedFolder: CGFloat = 40
    static let editTrack: CGFloat = 34
    static let reviewQuestion: CGFloat = 64
    static let linkedFromRadioMin: CGFloat = 34
}

enum Metrics {
    /// 1 pt separators everywhere.
    static let hairline: CGFloat = 1
    static let toolbarHeight: CGFloat = 52
    static let playerBarHeight: CGFloat = 64
    static let playerBarEpisodeHeight: CGFloat = 96
    static let columnHeaderHeight: CGFloat = 32
    static let panelHeaderHeight: CGFloat = 40
    static let dividerHeight: CGFloat = 7
    static let dividerGrabber = CGSize(width: 36, height: 3)
    static let toolbarButton = CGSize(width: 30, height: 28)
    static let importButton = CGSize(width: 34, height: 28)
    static let searchField = CGSize(width: 280, height: 28)
    static let segmentHeight: CGFloat = 24
    static let smallButtonHeight: CGFloat = 22
    static let standardButtonHeight: CGFloat = 28
    static let largeButtonHeight: CGFloat = 40
    static let coverSmall: CGFloat = 32
    static let cover: CGFloat = 40
    static let collapseButton: CGFloat = 24
    /// List rows sit inside this padding, with `rowPadding` inside each row.
    static let listInset: CGFloat = 6
    static let rowPadding: CGFloat = 8
    /// Space between stacked detail-panel sections.
    static let sectionSpacing: CGFloat = 22
    /// Artist filter field (height 24, radius 6) and the radio-only button beside it.
    static let filterFieldHeight: CGFloat = 24
    static let radioOnlyButton = CGSize(width: 26, height: 24)
    /// Track row grid `22px 26px minmax(0,1fr) 160px 46px` (§3.4).
    static let trackIconColumn: CGFloat = 22
    static let trackNumberColumn: CGFloat = 26
    static let trackNoteColumn: CGFloat = 160
    static let trackLengthColumn: CGFloat = 46
    /// Play-state icons (§3.5).
    static let playStateIcon: CGFloat = 14
    /// Loading skeletons appear only for loads slower than this (§11.3).
    static let skeletonDelay: Duration = .milliseconds(300)
    /// Type-to-select keeps typed letters together for this long.
    static let typeSelectTimeout: Duration = .seconds(1)
}

/// A track grid `minmax(minimum, fraction fr)` per column, as in the design's CSS grids.
nonisolated struct ColumnSpec: Equatable {
    var fractions: [CGFloat]
    var minimums: [CGFloat]
    /// Space between columns (CSS `gap`).
    var gap: CGFloat = 0

    /// Library lists and detail panels: 25/30/45, at least 300/340/460 (§3).
    static let library = ColumnSpec(fractions: [25, 30, 45], minimums: [300, 340, 460])
    /// Radio lists: 17/26/57, at least 200/280/380 (§6).
    static let radio = ColumnSpec(fractions: [17, 26, 57], minimums: [200, 280, 380])
    /// Player bar: 1/2/1, at least 240/420/240, 16 apart (§3.8).
    static let playerBar = ColumnSpec(fractions: [1, 2, 1], minimums: [240, 420, 240], gap: 16)

    var minimumWidth: CGFloat { minimums.reduce(0, +) + gap * CGFloat(max(minimums.count - 1, 0)) }
}

enum WindowMetrics {
    /// Plan decision 5: the window's minimum width is the sum of the column minimums.
    static let minimumWidth = max(ColumnSpec.library.minimumWidth, ColumnSpec.radio.minimumWidth)
    static let minimumHeight: CGFloat = 640
    static let minimumListsHeight: CGFloat = 160
    static let minimumPanelsHeight: CGFloat = 120
    static let defaultSize = CGSize(width: 1440, height: 900)
}

// MARK: - Helpers

extension Color {
    /// A colour with a light and a dark appearance, from 0xRRGGBB values.
    init(light: UInt32, dark: UInt32) {
        self.init(nsColor: .dynamic(light: light, dark: dark))
    }
}

// Nonisolated: AppKit may resolve dynamic colours off the main thread.
extension NSColor {
    nonisolated static func dynamic(light: UInt32, dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            NSColor(hex: appearance.isDark ? dark : light)
        }
    }

    /// The system accent colour blended into a background, per appearance.
    nonisolated static func accentBlend(light: UInt32, dark: UInt32, fraction: (light: CGFloat, dark: CGFloat))
        -> NSColor
    {
        NSColor(name: nil) { appearance in
            let isDark = appearance.isDark
            let base = NSColor(hex: isDark ? dark : light)
            return base.blended(withFraction: isDark ? fraction.dark : fraction.light, of: .controlAccentColor) ?? base
        }
    }

    nonisolated convenience init(hex: UInt32) {
        self.init(
            srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}

extension NSAppearance {
    nonisolated var isDark: Bool { bestMatch(from: [.aqua, .darkAqua]) == .darkAqua }
}

extension View {
    /// 11 pt semibold uppercase tertiary label (NOTE, TAGS · 3, DISC 2).
    func sectionLabelStyle() -> some View {
        font(Typography.sectionLabel)
            .tracking(Typography.sectionLabelTracking)
            .textCase(.uppercase)
            .foregroundStyle(Palette.text3)
    }

    func popoverShadow() -> some View { modifier(PopoverShadow()) }
}
