import HealthOpsCore
import SwiftUI

/// Question-and-answer screen. Shows only what the contract marks as display text: the data
/// status, the explanation status, the template answer, the cards and their warnings.
struct ChatView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var assistant: AssistantModel
    @FocusState private var composing: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                scopeBanner
                if assistant.turns.isEmpty {
                    ContentUnavailableView(
                        "Ask about your recorded history",
                        systemImage: "text.magnifyingglass",
                        description: Text("Examples: \"How did my resting heart rate compare with the week before?\", \"How much did I sleep last night?\" Answers come from the data cards, not from the model's memory."))
                } else {
                    ScrollViewReader { proxy in
                        List(assistant.turns) { turn in
                            VStack(alignment: .leading, spacing: 12) {
                                TurnView(turn: turn)
                            }.id(turn.id)
                                .listRowBackground(Color.clear)
                                .listRowSeparator(.hidden)
                        }
                        .listStyle(.plain)
                        .scrollContentBackground(.hidden)
                        .scrollDismissesKeyboard(.interactively)
                        .onTapGesture { composing = false }
                        .onChange(of: assistant.turns.count) { _, _ in
                            if let last = assistant.turns.last { proxy.scrollTo(last.id, anchor: .bottom) }
                        }
                    }
                }
                if let notice = assistant.notice {
                    Text(notice).font(.footnote).foregroundStyle(.secondary).padding(.horizontal)
                }
                composer
            }
            .background(AskAppearance.canvas)
            .navigationTitle("Ask")
            .toolbarBackground(AskAppearance.canvas, for: .navigationBar)
            .tint(.primary)
            .onAppear {
                if let url = model.serverURL, let token = model.currentToken() {
                    assistant.restore(serverURL: url, token: token)
                }
            }
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { composing = false }
                }
            }
        }
    }

    private var scopeBanner: some View {
        Group {
            if let user = model.user, let host = model.serverURL?.host {
                Text("Asking as \(user.displayName) on \(host). Only pending clarification is saved on this device.")
            } else if model.hasToken, let host = model.serverURL?.host {
                Text("Account not verified yet; questions go to \(host) with the stored token and any failure is shown here.")
            } else {
                Text("Enter the server address and a token on the Setup tab before asking.")
            }
        }
        .font(.footnote).foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal).padding(.top, 4)
    }

    private var composer: some View {
        VStack(spacing: 8) {
            Picker("Data source", selection: $assistant.provider) {
                ForEach(AssistantModel.ProviderChoice.allCases) { choice in
                    Text(choice.label).tag(choice)
                }
            }
            .pickerStyle(.segmented)
            if let response = assistant.pendingResponse,
               let form = response.clarification, form.version == 1, !assistant.busy {
                ClarificationView(form: form, busy: assistant.busy,
                                  canSubmit: model.hasToken && model.serverURL != nil,
                                  submit: { text in assistant.questionDraft = text; send() },
                                  cancel: { assistant.reset(reason: "clarification cancelled") })
                    .id("\(response.conversationID ?? "")-\(response.conversationVersion ?? 0)")
            } else {
                HStack {
                    TextField("Question about your recorded data", text: $assistant.questionDraft, axis: .vertical)
                        .lineLimit(1...4)
                        .textFieldStyle(.roundedBorder)
                        .focused($composing)
                        .submitLabel(.send)
                        .onSubmit(send)
                    if assistant.busy {
                        Button("Cancel", role: .cancel) { assistant.cancel() }
                    } else {
                        Button("New question") { assistant.reset(reason: "new question") }
                            .disabled(assistant.busy)
                        Button("Send", action: send)
                            .buttonStyle(AskButtonStyle(filled: true))
                            .disabled(!canSend)
                    }
                }
            }
        }
        .padding()
        .background(AskAppearance.canvas)
        .overlay(alignment: .top) { Rectangle().fill(Color.primary.opacity(0.25)).frame(height: 1) }
    }

    /// A stored token and a server address are enough to try; an unreachable server or a
    /// rejected token is reported as a failure in the conversation instead of a disabled button.
    private var canSend: Bool {
        model.hasToken && model.serverURL != nil
            && !assistant.questionDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func send() {
        guard canSend, let serverURL = model.serverURL, let token = model.currentToken() else { return }
        composing = false
        assistant.ask(serverURL: serverURL, token: token)
    }
}

struct TurnView: View {
    let turn: AssistantConversation.Turn

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                Text(turn.question).font(.body.bold())
                Spacer()
                Text(ProviderLabel.display(turn.provider)).font(.caption2).foregroundStyle(.secondary)
            }
            switch turn.outcome {
            case nil:
                ProgressView("Waiting for the server…").font(.caption)
            case let .failure(failure):
                Label(failure.message, systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.red)
            case let .response(response):
                AnswerView(response: response)
            }
        }
        .padding(.vertical, 6)
    }
}

struct AnswerView: View {
    let response: AskResponse

    private var presentation: AnswerPresentation { AnswerPresentation(response) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Chip(text: "data: \(presentation.dataStatusLabel)", tone: dataTone)
                Chip(text: explanationText, tone: explanationTone)
            }
            Text(response.answer).font(.callout)
            if !response.cards.isEmpty {
                DisclosureGroup("View evidence and sources") {
                    ForEach(response.cards) { card in CardView(card: card) }
                }
            }
            if !response.disclaimer.isEmpty {
                Text(response.disclaimer).font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private var dataTone: Chip.Tone {
        switch response.status {
        case .answered: return .good
        case .insufficientData, .needsClarification, .refused: return .neutral
        default: return .warning
        }
    }

    private var explanationText: String {
        switch presentation.explanationBadge {
        case .verified:
            return response.cards.contains(where: { $0.kind == .reference })
                ? "source match checked · not a safety review"
                : "explanation verified against the cards"
        case .unverified: return "explanation not verified; read the cards"
        case .notApplicable: return "no explanation step"
        }
    }

    private var explanationTone: Chip.Tone {
        switch presentation.explanationBadge {
        case .verified: return .good
        case .unverified: return .warning
        case .notApplicable: return .neutral
        }
    }
}

struct Chip: View {
    enum Tone { case good, warning, neutral }
    let text: String
    let tone: Tone

    var body: some View {
        Text(text)
            .font(.caption2.bold())
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.06), in: RoundedRectangle(cornerRadius: 5))
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(color.opacity(0.65), lineWidth: 1))
            .foregroundStyle(color)
    }

    private var color: Color {
        switch tone {
        case .good: return .primary
        case .warning: return .orange
        case .neutral: return .secondary
        }
    }
}

/// Deterministic data card. Values are the server's display strings; the phone never converts
/// units or sums rows.
struct CardView: View {
    let card: Card

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(card.title).font(.subheadline.bold())
            if let start = card.startDate, let end = card.endDate {
                Text("\(start) to \(end) · \(card.timezone)\(card.provider.map { " · \(ProviderLabel.display($0))" } ?? "")")
                    .font(.caption).foregroundStyle(.secondary)
            }
            switch card.kind {
            case .dailyTable: dailyTable
            case .comparison: comparison
            case .availability:
                Text("Availability summary; see the metrics listed by the server.")
                    .font(.caption).foregroundStyle(.secondary)
            case .reference: reference
            case .unknown:
                Text("Card type not supported by this app version.").font(.caption).foregroundStyle(.orange)
            }
            if let statistic = card.statistic {
                Text(statistic).font(.caption2).foregroundStyle(.secondary)
            }
            ForEach(card.warningText, id: \.self) { warning in
                Label(warning, systemImage: "info.circle").font(.caption2).foregroundStyle(.orange)
            }
        }
        .padding(10)
        .background(AskAppearance.paper, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.7), lineWidth: 1))
    }

    /// A quoted passage from the reference library: the quote is the server's text, cut to the
    /// source's quotation limit; the link opens the original.
    private var reference: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let text = card.text {
                Text("\u{201C}\(text)\u{201D}").font(.callout).italic()
            }
            HStack(spacing: 6) {
                if let publisher = card.publisher { Text(publisher).font(.caption).foregroundStyle(.secondary) }
                if let section = card.section { Text("· \(section)").font(.caption).foregroundStyle(.secondary) }
            }
            if let urlText = card.sourceURL, let url = URL(string: urlText) {
                Link("Open source", destination: url).font(.caption)
            }
            if let license = card.license {
                Text("Licence: \(license)").font(.caption2).foregroundStyle(.secondary)
            }
            Text("Reference material, not an assessment of your values.").font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var dailyTable: some View {
        VStack(spacing: 2) {
            ForEach(card.rows, id: \.date) { row in
                HStack {
                    Text(row.date).font(.caption.monospaced())
                    Spacer()
                    Text(row.display)
                        .font(.caption.monospaced())
                        .foregroundStyle(rowColor(row))
                    if row.quality == .unusable {
                        Text("\(row.records) record(s), not summarisable").font(.caption2).foregroundStyle(.orange)
                    }
                }
            }
        }
    }

    private func rowColor(_ row: CardRow) -> Color {
        switch row.quality {
        case .value: return .primary
        case .unusable: return .orange
        case .none: return .secondary
        case .unknown: return .orange
        }
    }

    private var comparison: some View {
        VStack(alignment: .leading, spacing: 4) {
            side("Baseline", card.baseline)
            side("Current", card.current)
            if let difference = card.difference {
                Text("Difference \(difference)\(directionText)").font(.caption.monospaced())
            } else {
                Text("Difference unavailable: a side has no value.").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var directionText: String {
        guard let direction = card.direction, direction != .unknown else { return "" }
        return " (\(direction.rawValue))"
    }

    private func side(_ name: String, _ side: ComparisonSide?) -> some View {
        HStack {
            Text("\(name) \(side?.startDate ?? "?")–\(side?.endDate ?? "?")").font(.caption.monospaced())
            Spacer()
            Text(side?.display ?? side?.value ?? "no data").font(.caption.monospaced())
            if let days = side?.daysWithData {
                Text("\(days) day(s) with data").font(.caption2).foregroundStyle(.secondary)
            }
        }
    }
}


/// Server-provided choices; only explicit selections become the user's next message.
struct ClarificationView: View {
    let form: ClarificationForm
    let busy: Bool
    let canSubmit: Bool
    let submit: (String) -> Void
    let cancel: () -> Void
    @State private var draft = ClarificationDraft()

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Let’s make it specific", systemImage: "slider.horizontal.3")
                .font(.subheadline.bold())
            // Keep long option lists scrollable without taking over the conversation.
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(form.fields) { field in
                        Text(field.label).font(.caption).foregroundStyle(.secondary)
                        if ["single", "multiple"].contains(field.selection) {
                            ScrollView(.horizontal) {
                                HStack(spacing: 6) {
                                    ForEach(field.options) { option in
                                        let selected = draft.selected[field.id, default: []].contains(option.id)
                                        Button {
                                            draft.toggle(option, in: field)
                                        } label: {
                                            HStack(spacing: 4) {
                                                if selected { Image(systemName: "checkmark") }
                                                Text(option.label).fixedSize()
                                            }
                                        }
                                        .buttonStyle(AskButtonStyle(filled: selected))
                                        .accessibilityAddTraits(selected ? .isSelected : [])
                                        .accessibilityHint(field.selection == "multiple" ? "Choose one or more" : "Choose one")
                                        .disabled(busy)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            .frame(maxHeight: 140)
            TextField("Or type your own dates, metric, or correction", text: $draft.custom, axis: .vertical)
                .textFieldStyle(.roundedBorder).lineLimit(1...2).disabled(busy)
            HStack {
                Button("Confirm scope") { submit(draft.reply(for: form)) }
                    .buttonStyle(AskButtonStyle(filled: true))
                    .disabled(busy || !canSubmit || draft.reply(for: form).isEmpty)
                Button("Cancel", role: .cancel, action: cancel).disabled(busy)
            }
        }
        .padding(12)
        .background(AskAppearance.paper, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color.primary, lineWidth: 1.5))
    }
}


/// Paper-and-ink palette. Native dark mode keeps readable inverse contrast.
private enum AskAppearance {
    static let canvas = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(white: 0.08, alpha: 1)
            : UIColor(red: 0.973, green: 0.961, blue: 0.945, alpha: 1)
    })
    static let paper = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(white: 0.13, alpha: 1)
            : UIColor(red: 1, green: 0.992, blue: 0.980, alpha: 1)
    })
    static let inverseInk = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark ? .black : .white
    })
}

private struct AskButtonStyle: ButtonStyle {
    var filled = false
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 12).padding(.vertical, 10)
            .frame(minHeight: 44)
            .foregroundStyle(filled ? AskAppearance.inverseInk : Color.primary)
            .background(filled ? Color.primary : AskAppearance.paper,
                        in: RoundedRectangle(cornerRadius: 7))
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(Color.primary, lineWidth: 1))
            .opacity(!enabled ? 0.4 : configuration.isPressed ? 0.7 : 1)
    }
}
