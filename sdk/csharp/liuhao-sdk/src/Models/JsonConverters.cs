using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace LiuHao.Sdk.Models;

/// <summary>
/// A <see cref="JsonNamingPolicy"/> that converts PascalCase identifiers to
/// snake_case (e.g. <c>SystemPrompt</c> → <c>system_prompt</c>).
/// </summary>
/// <remarks>
/// .NET 6's <see cref="JsonNamingPolicy"/> does not ship a built-in
/// snake_case policy, so the SDK provides its own implementation for
/// serializing enum values in the wire format expected by the LiuHao API.
/// </remarks>
public sealed class SnakeCaseNamingPolicy : JsonNamingPolicy
{
    /// <summary>
    /// The shared singleton instance.
    /// </summary>
    public static readonly SnakeCaseNamingPolicy Instance = new();

    /// <inheritdoc/>
    public override string ConvertName(string name)
    {
        if (string.IsNullOrEmpty(name))
        {
            return name;
        }

        var builder = new StringBuilder(name.Length + 8);
        for (var i = 0; i < name.Length; i++)
        {
            var c = name[i];
            if (char.IsUpper(c))
            {
                if (i > 0
                    && (char.IsLower(name[i - 1])
                        || (i + 1 < name.Length && char.IsLower(name[i + 1]))))
                {
                    builder.Append('_');
                }

                builder.Append(char.ToLowerInvariant(c));
            }
            else
            {
                builder.Append(c);
            }
        }

        return builder.ToString();
    }
}

/// <summary>
/// Attribute that serializes an enum as a snake_case string using
/// <see cref="JsonStringEnumConverter"/>.
/// </summary>
/// <remarks>
/// Apply to enum declarations whose wire representation must be snake_case
/// strings, e.g. <c>AgentStatus.Active</c> → <c>"active"</c>.
/// </remarks>
[AttributeUsage(AttributeTargets.Enum, AllowMultiple = false)]
public sealed class SnakeCaseEnumConverterAttribute : JsonConverterAttribute
{
    /// <inheritdoc/>
    public override JsonConverter CreateConverter(Type typeToConvert)
    {
        return new JsonStringEnumConverter(SnakeCaseNamingPolicy.Instance);
    }
}

/// <summary>
/// Formats enum values in the snake_case wire representation expected by
/// the LiuHao API (e.g. <c>AgentRunStatus.TimedOut</c> → <c>timed_out</c>).
/// </summary>
internal static class EnumWireFormatter
{
    /// <summary>
    /// Converts an enum value to its snake_case wire representation.
    /// </summary>
    /// <param name="value">The enum value.</param>
    /// <returns>The snake_case string.</returns>
    public static string ToSnakeCase(this Enum value) =>
        SnakeCaseNamingPolicy.Instance.ConvertName(value.ToString());
}

/// <summary>
/// Provides the shared <see cref="JsonSerializerOptions"/> used by the SDK
/// for all request and response payloads.
/// </summary>
public static class LiuHaoJsonSerializerOptions
{
    /// <summary>
    /// The shared default options instance.
    /// </summary>
    /// <remarks>
    /// Property names are mapped explicitly via
    /// <see cref="JsonPropertyNameAttribute"/> throughout the model layer;
    /// the options mainly control null handling, number reading, and
    /// tolerance for case-insensitive deserialization.
    /// </remarks>
    public static readonly JsonSerializerOptions Default = CreateDefault();

    private static JsonSerializerOptions CreateDefault()
    {
        return new JsonSerializerOptions
        {
            PropertyNamingPolicy = null,
            PropertyNameCaseInsensitive = true,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
            NumberHandling = JsonNumberHandling.AllowReadingFromString,
            WriteIndented = false,
        };
    }
}
