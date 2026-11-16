import SwiftUI

/// The main window's toolbar (design §3.1): back, forward, import; the Library | Radio
/// switch centred; the search field on the right. Import (stage 9) and search (stage 8)
/// come later.
struct MainToolbar: ToolbarContent {
    @Binding var section: AppSection
    /// The shown view's history (Library or Radio).
    let history: any HistoryNavigating
    /// Opens the search palette.
    let search: () -> Void

    var body: some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            // ⌘[ / ⌘] are on the Go menu items (AppCommands), which call the same methods.
            Button("Back", systemImage: "chevron.left") { history.goBack() }
                .help("Back (⌘[)")
                .accessibilityLabel("Back (⌘[)")
                .accessibilityIdentifier("toolbar.back")
                .disabled(!history.canGoBack)
            Button("Forward", systemImage: "chevron.right") { history.goForward() }
                .help("Forward (⌘])")
                .accessibilityLabel("Forward (⌘])")
                .accessibilityIdentifier("toolbar.forward")
                .disabled(!history.canGoForward)
            Button("Import", systemImage: "plus") {}
                .help("Import")
                .accessibilityIdentifier("toolbar.import")
                .disabled(true)
        }

        ToolbarItem(placement: .principal) {
            Picker("View", selection: $section) {
                ForEach(AppSection.allCases) { section in
                    Text(section.title).tag(section)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .accessibilityLabel("Library or Radio")
            .accessibilityIdentifier("toolbar.section")
        }

        ToolbarItem(placement: .automatic) {
            SearchFieldButton(action: search)
                .fixedSize()
        }
    }
}

/// Looks like a search field; opens the search palette (⌘K and ⌘F, decision 9). A plain
/// view with a tap, not a `Button`: a custom-label button collapses to nothing in a macOS
/// toolbar item.
private struct SearchFieldButton: View {
    let action: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 12))
                .foregroundStyle(Palette.text3)
            Text("Search")
                .font(Typography.body)
                .foregroundStyle(Palette.text3)
            Spacer(minLength: 0)
            Text("⌘K")
                .font(Typography.meta)
                .foregroundStyle(Palette.text3)
                .padding(.horizontal, 4)
                .overlay(
                    RoundedRectangle(cornerRadius: Radius.chip)
                        .strokeBorder(Palette.line, lineWidth: Metrics.hairline)
                )
        }
        .padding(.horizontal, 8)
        .frame(width: Metrics.searchField.width, height: Metrics.searchField.height)
        .background(Palette.field, in: RoundedRectangle(cornerRadius: Radius.searchField))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.searchField)
                .strokeBorder(Palette.line, lineWidth: Metrics.hairline)
        )
        .contentShape(Rectangle())
        .onTapGesture(perform: action)
        .help("Search (⌘K)")
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { action() }
        .accessibilityLabel("Search library")
        .accessibilityIdentifier("toolbar.search")
    }
}
