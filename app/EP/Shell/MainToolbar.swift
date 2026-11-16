import SwiftUI

/// The main window's toolbar (design §3.1): back, forward, import; the Library | Radio
/// switch centred; the search field on the right. Only the switch works yet: history
/// (stage 3), import (stage 9) and search (stage 8) come later.
struct MainToolbar: ToolbarContent {
    @Binding var section: AppSection

    var body: some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            Button("Back", systemImage: "chevron.left") {}
                .help("Back (⌘[)")
                .accessibilityLabel("Back (⌘[)")
                .accessibilityIdentifier("toolbar.back")
                .disabled(true)
            Button("Forward", systemImage: "chevron.right") {}
                .help("Forward (⌘])")
                .accessibilityLabel("Forward (⌘])")
                .accessibilityIdentifier("toolbar.forward")
                .disabled(true)
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
            SearchFieldButton()
                .fixedSize()
        }
    }
}

/// Looks like a search field; opens the search palette (stage 8; ⌘K and ⌘F, decision 9).
/// A plain view, not a `Button`: a custom-label button collapses to nothing in a macOS
/// toolbar item. Inert until stage 8 adds the tap and shortcuts.
private struct SearchFieldButton: View {
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
        .help("Search (⌘K)")
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel("Search library")
        .accessibilityIdentifier("toolbar.search")
    }
}
