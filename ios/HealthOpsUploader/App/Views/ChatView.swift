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
                            TurnView(turn: turn).id(turn.id)
                        }
                        .listStyle(.plain)
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
            .navigationTitle("Ask")
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
                Text("Asking as \(user.displayName) on \(host). History is kept in memory only.")
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
                    Button("Send", action: send)
                        .buttonStyle(.borderedProminent)
                        .disabled(!canSend)
                }
            }
        }
        .padding()
        .background(.bar)
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
            ForEach(response.cards) { card in CardView(card: card) }
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
        case .verified: return "explanation verified against the cards"
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
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }

    private var color: Color {
        switch tone {
        case .good: return .green
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
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 10))
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
