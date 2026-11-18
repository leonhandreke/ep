import EPAPI
import SwiftUI

/// The Radio view's detail panel (§6, decision 1): one full-width panel for the selected
/// episode, or the show when no episode is selected. Header: kind badge, title, "Late
/// Night Tapes · 14 Mar 2025 · London", and the tabs "Note", "Tags 2", "Linked from 3",
/// each holding the Library panels' section of that name.
struct RadioDetailPanel: View {
    let details: EntityDetails
    @Binding var collapsed: Bool

    @Environment(RadioBrowser.self) private var radio
    @SceneStorage("radioDetailTab") private var tab = RadioDetailTab.note

    var body: some View {
        VStack(spacing: 0) {
            header
            if !collapsed {
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            }
        }
        .background(Palette.bg)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(badge))
        .accessibilityIdentifier("detail.radio")
    }

    private var isEpisode: Bool { radio.location.episodeID != nil }
    private var badge: LocalizedStringKey { isEpisode ? "Episode" : "Show" }
    private var id: String { isEpisode ? "detail.episode" : "detail.show" }

    private var title: String? {
        if let episodeID = radio.location.episodeID {
            return radio.episode.flatMap { $0.id == episodeID ? $0.name : nil }
                ?? radio.episodes.first { $0.id == episodeID }?.name
        }
        guard let showID = radio.location.showID else { return nil }
        return radio.shows.first { $0.id == showID }?.name
    }

    private var meta: String? {
        if let episodeID = radio.location.episodeID {
            guard let episode = radio.episodes.first(where: { $0.id == episodeID }) else { return nil }
            return [radio.show?.name, RadioFormat.day(episode.broadcastAt), episode.location]
                .compactMap { $0?.isEmpty == false ? $0 : nil }.joined(separator: " · ")
        }
        guard let show = radio.show, show.id == radio.location.showID else { return nil }
        return String(localized: "\(show.station.displayName) · \(LibraryFormat.count(show.episodeCount)) episodes")
    }

    private var header: some View {
        HStack(spacing: 8) {
            KindBadge(label: badge)
            TextRow(spacing: 8) {
                if let title {
                    Text(title)
                        .font(Typography.panelTitle)
                        .foregroundStyle(Palette.text)
                        .lineLimit(1)
                        .accessibilityIdentifier("\(id).title")
                }
                if let meta {
                    Text(meta)
                        .font(Typography.body)
                        .foregroundStyle(Palette.text2)
                        .lineLimit(1)
                }
            }
            if details.ref != nil {
                tabs.padding(.leading, 20)
            }
            Spacer(minLength: 0)
            CollapsePanelsButton(id: "detail.radio.collapse", collapsed: $collapsed)
        }
        .padding(.leading, 14)
        .padding(.trailing, 8)
        .frame(height: Metrics.panelHeaderHeight)
        .overlay(alignment: .bottom) { HairlineDivider() }
        .overlay(alignment: .top) {
            if collapsed { HairlineDivider() }
        }
    }

    private var tabs: some View {
        HStack(spacing: 2) {
            ForEach(RadioDetailTab.allCases, id: \.self) { item in
                let selected = item == tab
                Button {
                    tab = item
                    collapsed = false
                } label: {
                    Text(label(for: item))
                        .font(selected ? Typography.name : Typography.body)
                        .foregroundStyle(selected ? Palette.text : Palette.text2)
                        .padding(.horizontal, 12)
                        .frame(height: 26)
                        .background(selected ? Palette.chip : .clear, in: RoundedRectangle(cornerRadius: Radius.button))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(selected ? [.isSelected] : [])
                .accessibilityIdentifier("detail.radio.tab.\(item.rawValue)")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Detail sections")
    }

    private func label(for tab: RadioDetailTab) -> String {
        switch tab {
        case .note: String(localized: "Note")
        case .tags: String(localized: "Tags \(details.tags.count)")
        case .linkedFrom: String(localized: "Linked from \(details.backlinks.count + details.radioAppearances.count)")
        }
    }

    @ViewBuilder private var content: some View {
        switch details.phase {
        case .idle:
            Color.clear
        case .loading where details.entity == nil:
            DelayedSkeleton(rowHeight: RowHeight.track, rows: 3)
        case .failed(let message) where details.entity == nil:
            ColumnMessage(id: id, text: message) { details.reload() }
        default:
            ScrollView {
                Group {
                    switch tab {
                    case .note: NoteSection(details: details, id: id)
                    case .tags: TagsSection(details: details, id: id)
                    case .linkedFrom: LinkedFromSection(details: details, id: id, kind: isEpisode ? .episode : .show)
                    }
                }
                .environment(\.detailSectionTitles, false)
                .padding(.top, 14)
                .padding(.horizontal, 18)
                .padding(.bottom, 24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

enum RadioDetailTab: String, CaseIterable {
    case note, tags, linkedFrom
}
