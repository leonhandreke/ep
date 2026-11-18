import SwiftUI

/// The player bar (§3.8, board 3): 64 pt, 96 pt for a radio episode; `--bar`, top border,
/// on the 1fr/2fr/1fr grid. Left: cover, context line, title, artist and album (each
/// navigates). Centre: transport over the scrubber with elapsed and remaining time. Right:
/// queue, volume. Instead of what plays it can show why a track did not play, or that
/// nothing plays.
struct PlayerBar: View {
    /// Shows what plays in this window's Library (title click, ⌘L).
    let showPlaying: () -> Void

    @Environment(Player.self) private var player

    var body: some View {
        let isEpisode = player.current?.isEpisode == true && player.notice == nil
        Group {
            if let notice = player.notice {
                HStack(spacing: 16) {
                    NoticeView(notice: notice)
                    PlayerControls()
                }
            } else if player.current != nil {
                ColumnGrid(spec: .playerBar) {
                    NowPlayingInfo(showPlaying: showPlaying)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
                    VStack(spacing: 4) {
                        Transport()
                        ScrubberRow()
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    PlayerControls()
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .trailing)
                }
                .padding(.bottom, isEpisode ? 12 : 0)
            } else {
                HStack(spacing: 16) {
                    NothingPlaying()
                    PlayerControls()
                }
            }
        }
        .padding(.horizontal, 16)
        .frame(height: isEpisode ? Metrics.playerBarEpisodeHeight : Metrics.playerBarHeight)
        .frame(maxWidth: .infinity)
        .background(Palette.bar)
        .overlay(alignment: .top) { HairlineDivider() }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Player")
        .accessibilityIdentifier("player.bar")
    }
}

// MARK: - Left

private struct NowPlayingInfo: View {
    let showPlaying: () -> Void
    @Environment(Player.self) private var player
    @State private var hovering = false

    var body: some View {
        let item = player.current
        let navigable = player.canShowPlaying
        HStack(spacing: 10) {
            CoverThumbnail(
                path: item?.coverPath, size: Metrics.cover,
                radius: item?.isEpisode == true ? Metrics.cover / 2 : Radius.cover)
            VStack(alignment: .leading, spacing: 1) {
                if let context = player.context {
                    Text(context)
                        .font(Typography.meta)
                        .monospacedDigit()
                        .foregroundStyle(Palette.text3)
                        .lineLimit(1)
                        .accessibilityIdentifier("player.context")
                }
                Button(action: showPlaying) {
                    Text(player.title)
                        .font(Typography.name)
                        .foregroundStyle(Palette.text)
                        .underline(hovering && navigable)
                        .lineLimit(1)
                }
                .buttonStyle(.plain)
                .onHover { hovering = $0 }
                .pointerStyle(navigable ? .link : nil)
                .help(navigable ? String(localized: "Show playing") : "")
                .accessibilityLabel(player.title)
                .accessibilityHint(navigable ? String(localized: "Shows what is playing") : "")
                .accessibilityIdentifier("player.title")
                subtitle
            }
        }
    }

    /// Artist and album (an episode's show), each a link like the title.
    private var subtitle: some View {
        HStack(spacing: 0) {
            ForEach(Array(player.subtitleLinks.enumerated()), id: \.offset) { index, link in
                if index > 0 {
                    Text(verbatim: " — ").foregroundStyle(Palette.text2).accessibilityHidden(true)
                }
                SubtitleLink(link: link, id: "player.subtitle.\(index)")
            }
            if let note = player.subtitleNote {
                Text(verbatim: " · \(note)")
                    .foregroundStyle(Palette.text3)
            }
        }
        .font(Typography.secondary)
        .lineLimit(1)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(player.subtitle)
        .accessibilityIdentifier("player.subtitle")
    }
}

/// One part of the player's second line: shows its artist, album or show when clicked
/// (⌥⌘-click: in a new window), underlined while hovered, like the title.
private struct SubtitleLink: View {
    let link: PlayerLink
    let id: String
    @Environment(\.openEntity) private var openEntity
    @State private var hovering = false

    var body: some View {
        let navigable = link.target != nil && openEntity != nil
        Button {
            guard let target = link.target else { return }
            openEntity?(target, newWindow: NSEvent.modifierFlags.contains([.command, .option]))
        } label: {
            Text(link.text)
                .foregroundStyle(Palette.text2)
                .underline(hovering && navigable)
        }
        .buttonStyle(.plain)
        .disabled(!navigable)
        .onHover { hovering = $0 }
        .pointerStyle(navigable ? .link : nil)
        .help(navigable ? String(localized: "Show \(link.text)") : "")
        .accessibilityLabel(link.text)
        .accessibilityIdentifier(id)
    }
}

// MARK: - Centre

private struct Transport: View {
    @Environment(Player.self) private var player

    var body: some View {
        let hasEntries = player.current?.entries.isEmpty == false
        HStack(spacing: 14) {
            Button {
                player.previous()
            } label: {
                Image(systemName: "backward.end.fill").font(.system(size: 13))
            }
            .accessibilityLabel(hasEntries ? "Previous entry" : "Previous")
            .help(hasEntries ? "Previous entry" : "Previous")
            .accessibilityIdentifier("player.previous")

            Button {
                player.togglePlayPause()
            } label: {
                Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.bg)
                    .frame(width: 30, height: 30)
                    .background(Palette.text, in: Circle())
                    .opacity(player.isLoading ? 0.5 : 1)
            }
            .accessibilityLabel(player.isPlaying ? "Pause (Space)" : "Play (Space)")
            .accessibilityIdentifier("player.playPause")

            Button {
                player.next()
            } label: {
                Image(systemName: "forward.end.fill").font(.system(size: 13))
            }
            .accessibilityLabel(hasEntries ? "Next entry" : "Next")
            .help(hasEntries ? "Next entry" : "Next")
            .accessibilityIdentifier("player.next")
        }
        .buttonStyle(.plain)
        .foregroundStyle(Palette.text2)
    }
}

private struct ScrubberRow: View {
    @Environment(Player.self) private var player

    var body: some View {
        HStack(spacing: 8) {
            Text(LibraryFormat.duration(ms: player.elapsedMS) ?? "")
                .frame(minWidth: 40, alignment: .trailing)
                .accessibilityIdentifier("player.elapsed")
            Scrubber()
            Text("−\(LibraryFormat.duration(ms: player.remainingMS) ?? "")")
                .frame(minWidth: 40, alignment: .leading)
                .accessibilityIdentifier("player.remaining")
        }
        .font(Typography.meta)
        .monospacedDigit()
        .foregroundStyle(Palette.text2)
    }
}

/// The scrubber: a 4 pt track with neutral progress; for long files the current entry's
/// range as an accent band and a mark per entry, filled for an exact time and hollow
/// for an estimated one, with a tooltip on hover.
private struct Scrubber: View {
    @Environment(Player.self) private var player
    @State private var dragMS: Int?
    @State private var hovered: PlayItem.Entry?
    @State private var hoverX: CGFloat = 0

    private static let height: CGFloat = 14

    var body: some View {
        let range = player.scrubberRange
        let item = player.current
        let marks =
            (item?.coversWholeFile == true ? item?.entries : nil)?.filter { $0.startMS > range.lowerBound } ?? []
        GeometryReader { geometry in
            let width = geometry.size.width
            let x = { (ms: Int) -> CGFloat in
                let span = CGFloat(max(range.upperBound - range.lowerBound, 1))
                return min(max(CGFloat(ms - range.lowerBound) / span, 0), 1) * width
            }
            ZStack(alignment: .leading) {
                Capsule().fill(Palette.line).frame(height: 4)
                Capsule().fill(Palette.text2).frame(width: x(dragMS ?? player.positionMS), height: 4)
                if let band {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Palette.accent.opacity(0.35))
                        .frame(width: max(x(band.upperBound) - x(band.lowerBound), 2), height: 8)
                        .offset(x: x(band.lowerBound))
                }
                ForEach(marks) { mark in
                    RoundedRectangle(cornerRadius: 1)
                        .fill(mark.isEstimated ? Palette.bar : Palette.text2)
                        .overlay(RoundedRectangle(cornerRadius: 1).strokeBorder(Palette.text2, lineWidth: 1))
                        .frame(width: 4, height: 14)
                        .offset(x: x(mark.startMS) - 2)
                }
            }
            .frame(width: width, height: Self.height)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { dragMS = ms(at: $0.location.x, width: width, range: range) }
                    .onEnded {
                        player.seek(to: ms(at: $0.location.x, width: width, range: range))
                        dragMS = nil
                    }
            )
            .onContinuousHover { phase in
                switch phase {
                case .active(let point):
                    let nearest = marks.min { abs(x($0.startMS) - point.x) < abs(x($1.startMS) - point.x) }
                    hovered = nearest.flatMap { abs(x($0.startMS) - point.x) <= 5 ? $0 : nil }
                    hoverX = hovered.map { x($0.startMS) } ?? 0
                case .ended:
                    hovered = nil
                }
            }
            .overlay(alignment: .topLeading) {
                if let hovered {
                    let markX = hoverX
                    MarkTooltip(entry: hovered)
                        .fixedSize()
                        .alignmentGuide(.leading) { $0.width / 2 - markX }
                        .alignmentGuide(.top) { $0.height + 6 }
                        .allowsHitTesting(false)
                }
            }
        }
        .frame(height: Self.height)
        .zIndex(1)
        .accessibilityElement()
        .accessibilityLabel(accessibilityLabel(marks: marks))
        .accessibilityValue(
            "\(LibraryFormat.duration(ms: player.elapsedMS) ?? "") of \(LibraryFormat.duration(ms: range.upperBound - range.lowerBound) ?? "")"
        )
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: player.seek(to: player.positionMS + 10_000)
            case .decrement: player.seek(to: player.positionMS - 10_000)
            @unknown default: break
            }
        }
        .accessibilityIdentifier("player.scrubber")
    }

    /// The current entry's range on an episode's scrubber, or a lone range's span.
    private var band: ClosedRange<Int>? {
        guard let item = player.current, item.coversWholeFile else { return nil }
        if item.isEpisode, let entry = player.currentEntry {
            let end = entry.endMS ?? item.entry(after: entry.startMS)?.startMS ?? player.scrubberRange.upperBound
            return entry.startMS...max(end, entry.startMS)
        }
        if case .singleFile = item.kind, item.entries.count == 1, let end = item.spanEndMS {
            return item.startMS...max(end, item.startMS)
        }
        return nil
    }

    private func ms(at x: CGFloat, width: CGFloat, range: ClosedRange<Int>) -> Int {
        let fraction = min(max(x / max(width, 1), 0), 1)
        return range.lowerBound + Int(fraction * CGFloat(range.upperBound - range.lowerBound))
    }

    private func accessibilityLabel(marks: [PlayItem.Entry]) -> String {
        guard let item = player.current else { return String(localized: "Scrubber") }
        let estimated = marks.filter(\.isEstimated).count
        if item.isEpisode {
            let count = item.entries.count
            return estimated > 0
                ? String(localized: "Episode scrubber, \(count) tracklist marks, \(estimated) estimated")
                : String(localized: "Episode scrubber, \(count) tracklist marks")
        }
        if case .singleFile = item.kind {
            return String(localized: "Scrubber over the whole file, \(item.entries.count) track marks")
        }
        return String(localized: "Scrubber")
    }
}

/// "~58:40 · Jon Hassell — Dreamworld (time estimated)", inverted.
private struct MarkTooltip: View {
    let entry: PlayItem.Entry

    var body: some View {
        let time = (entry.isEstimated ? "~" : "") + (LibraryFormat.duration(ms: entry.startMS) ?? "")
        let suffix = entry.isEstimated ? String(localized: " (time estimated)") : ""
        Text(verbatim: "\(time) · \(entry.artist) — \(entry.title)\(suffix)")
            .font(Typography.meta)
            .monospacedDigit()
            .foregroundStyle(Palette.bg)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Palette.text, in: RoundedRectangle(cornerRadius: Radius.button))
            .accessibilityIdentifier("player.scrubber.tooltip")
    }
}

// MARK: - Right

/// The queue button and volume (plan decision 3).
private struct PlayerControls: View {
    @Environment(Player.self) private var player
    @State private var showsQueue = false

    var body: some View {
        @Bindable var player = player
        HStack(spacing: 12) {
            Button {
                showsQueue.toggle()
            } label: {
                HStack(spacing: 3) {
                    Image(systemName: "list.bullet").font(.system(size: 13))
                    if !player.queue.isEmpty {
                        Text(verbatim: "\(player.queue.count)").font(Typography.meta).monospacedDigit()
                    }
                }
                .foregroundStyle(Palette.text2)
                .frame(minWidth: 26, minHeight: 26)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Up next")
            .accessibilityLabel(queueLabel)
            .accessibilityIdentifier("player.queue")
            .popover(isPresented: $showsQueue, arrowEdge: .top) { QueuePopover() }
            HStack(spacing: 6) {
                Image(systemName: "speaker.wave.1")
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.text3)
                    .accessibilityHidden(true)
                Slider(value: $player.volume, in: 0...1)
                    .controlSize(.mini)
                    .frame(width: 90)
                    .accessibilityLabel("Volume")
                    .accessibilityIdentifier("player.volume")
            }
        }
        .fixedSize()
    }

    private var queueLabel: String {
        player.queue.count == 1
            ? String(localized: "Queue — 1 up next") : String(localized: "Queue — \(player.queue.count) up next")
    }
}

/// The queue (plan decision 8): what comes next, each removable, and Clear.
private struct QueuePopover: View {
    @Environment(Player.self) private var player

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Up next · \(player.queue.count)").sectionLabelStyle()
                Spacer()
                Button("Clear") { player.clearQueue() }
                    .controlSize(.small)
                    .disabled(player.queue.isEmpty)
                    .accessibilityIdentifier("player.queue.clear")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            HairlineDivider()
            if player.queue.isEmpty {
                Text("Nothing up next.")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
                    .frame(maxWidth: .infinity, minHeight: 60)
                    .accessibilityIdentifier("player.queue.empty")
            } else {
                ScrollView {
                    VStack(spacing: 0) {
                        ForEach(Array(player.queue.enumerated()), id: \.element.id) { index, item in
                            row(item, index: index)
                        }
                    }
                    .padding(4)
                }
                .frame(maxHeight: 320)
            }
        }
        .frame(width: 320)
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("player.queue.popover")
    }

    private func row(_ item: PlayItem, index: Int) -> some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(item.title).font(Typography.rowTitle).foregroundStyle(Palette.text).lineLimit(1)
                Text(item.subtitle).font(Typography.meta).foregroundStyle(Palette.text3).lineLimit(1)
            }
            Spacer(minLength: 0)
            Button {
                player.removeFromQueue(item.id)
            } label: {
                Image(systemName: "xmark").font(.system(size: 9, weight: .semibold))
                    .frame(width: 18, height: 18)
                    .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(Palette.text3)
            .help("Remove from the queue")
            .accessibilityLabel("Remove \(item.title) from the queue")
            .accessibilityIdentifier("player.queue.remove.\(index)")
        }
        .padding(.horizontal, 8)
        .frame(height: RowHeight.releaseGroup)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("player.queue.row.\(index)")
    }
}

// MARK: - States

/// Board 3, "nothing playing".
private struct NothingPlaying: View {
    var body: some View {
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: Radius.cover)
                .strokeBorder(Palette.text3, style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                .frame(width: Metrics.cover, height: Metrics.cover)
                .accessibilityHidden(true)
            Text("Double-click or press Return on a track to play.")
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .lineLimit(2)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("player.nothingPlaying")
    }
}

/// Board 3, "tried to play a link-only track", and the like for a missing file, no
/// source, or a failure.
private struct NoticeView: View {
    let notice: Player.Notice
    @Environment(\.openURL) private var openURL
    @Environment(\.openImport) private var openImport

    var body: some View {
        HStack(spacing: 10) {
            icon.frame(width: 22).accessibilityHidden(true)
            message
                .font(Typography.body)
                .foregroundStyle(Palette.text)
                .lineLimit(2)
                .accessibilityIdentifier("player.notice.text")
            if case .linkOnly(_, let kind, let url) = notice {
                Button("Open in \(kind.displayName)") { openURL(url) }
                    .controlSize(.small)
                    .accessibilityIdentifier("player.notice.open")
                Button("Import…") { openImport?(url.absoluteString) }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .disabled(openImport == nil)
                    .accessibilityIdentifier("player.notice.import")
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("player.notice")
    }

    @ViewBuilder private var icon: some View {
        switch notice {
        case .linkOnly:
            Image(systemName: "arrow.up.right").font(.system(size: 15, weight: .semibold)).foregroundStyle(
                Palette.text3)
        case .fileMissing, .failed:
            Image(systemName: "exclamationmark.circle").font(.system(size: 18)).foregroundStyle(Palette.danger)
        case .noSource:
            Image(systemName: "circle.dashed").font(.system(size: 18)).foregroundStyle(Palette.text3)
        }
    }

    private var message: Text {
        switch notice {
        case .linkOnly(let title, let kind, _):
            Text("\(Text(title).bold()) has no audio here — only a \(kind.displayName) link.")
        case .fileMissing(let title):
            Text("\(Text(title).bold()) can’t play — its file is missing.")
        case .noSource(let title):
            Text("\(Text(title).bold()) has no audio here.")
        case .failed(let title, let reason):
            Text("\(Text(title).bold()) stopped: \(reason)")
        }
    }
}
