import SwiftUI

/// The two views of the main window, switched in the toolbar.
enum AppSection: String, CaseIterable, Identifiable {
    case library, radio

    var id: Self { self }

    var title: LocalizedStringKey {
        switch self {
        case .library: "Library"
        case .radio: "Radio"
        }
    }

    var columns: ColumnSpec {
        switch self {
        case .library: .library
        case .radio: .radio
        }
    }
}

/// The main window (design §3): toolbar, the unreachable banner, three list columns, the
/// divider, the detail panels and the player bar. Each window keeps its own section,
/// panel height and collapsed state.
struct MainWindow: View {
    @Environment(ServerConnection.self) private var connection
    @SceneStorage("section") private var section = AppSection.library
    @SceneStorage("detailPanelsCollapsed") private var detailPanelsCollapsed = false
    /// Detail panel height in points; 0 until first laid out (then half the space).
    @SceneStorage("detailPanelsHeight") private var detailPanelsHeight: Double = 0

    var body: some View {
        VStack(spacing: 0) {
            if case .unreachable = connection.state {
                UnreachableBanner()
            }
            GeometryReader { geometry in
                let available = geometry.size.height - Metrics.dividerHeight
                let panels = clampedPanelsHeight(available: available)
                VStack(spacing: 0) {
                    ListColumns(section: section)
                        .frame(maxHeight: .infinity)
                    if detailPanelsCollapsed {
                        DetailPanels(section: section, collapsed: $detailPanelsCollapsed)
                            .frame(height: Metrics.panelHeaderHeight)
                    } else {
                        DetailDivider(height: panels, available: available) { detailPanelsHeight = $0 }
                        DetailPanels(section: section, collapsed: $detailPanelsCollapsed)
                            .frame(height: panels)
                    }
                }
            }
            PlayerBar()
        }
        .background(Palette.bg)
        .frame(minWidth: WindowMetrics.minimumWidth, minHeight: WindowMetrics.minimumHeight)
        .navigationTitle("EP")
        .toolbar { MainToolbar(section: $section) }
        .toolbarBackground(Palette.bar, for: .windowToolbar)
        .focusedSceneValue(\.detailPanelsCollapsed, $detailPanelsCollapsed)
    }

    private func clampedPanelsHeight(available: CGFloat) -> CGFloat {
        let wanted = detailPanelsHeight > 0 ? detailPanelsHeight : available / 2
        let maximum = max(WindowMetrics.minimumPanelsHeight, available - WindowMetrics.minimumListsHeight)
        return min(max(wanted, WindowMetrics.minimumPanelsHeight), maximum)
    }
}

#Preview {
    MainWindow()
        .environment(ServerConnection(serverURL: AppSettings.defaultServerURL))
        .frame(width: 1440, height: 900)
}
