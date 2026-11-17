// Open enums: schemas a newer server may extend (EPOpenAPINormalize's `openEnums`) are
// generated as the generator's open-enum pattern, a struct with `value1` (the known
// case) and `value2` (the raw string). An unknown value decodes instead of failing the
// whole response; the app checks `known` and treats `nil` generically.

/// A generated open enum: a known case, or a raw value this client does not know.
public protocol OpenEnum: Hashable, Sendable {
    associatedtype Known: RawRepresentable & Hashable & Sendable where Known.RawValue == String
    var value1: Known? { get }
    var value2: String? { get }
    init(value1: Known?, value2: String?)
}

extension OpenEnum {
    /// A known value, as decoding would produce it (both payloads set).
    public init(_ known: Known) { self.init(value1: known, value2: known.rawValue) }

    /// The known case; nil for a value this client does not know.
    public var known: Known? { value1 }

    /// The value as sent.
    public var rawValue: String { value2 ?? value1?.rawValue ?? "" }

    public static func == (lhs: Self, rhs: Known) -> Bool { lhs.known == rhs }
    public static func != (lhs: Self, rhs: Known) -> Bool { lhs.known != rhs }
}

extension Components.Schemas.IssueKind: OpenEnum {}
extension Components.Schemas.JobState: OpenEnum {}
extension Components.Schemas.Choice: OpenEnum {}
