namespace LiuHao.Sdk;

/// <summary>
/// Internal argument validation helpers.
/// </summary>
/// <remarks>
/// Provides equivalents of convenience guard APIs introduced after
/// .NET 6 (e.g. <c>ArgumentException.ThrowIfNullOrEmpty</c>) so the SDK
/// can target .NET 6 while keeping call sites concise.
/// </remarks>
internal static class Guard
{
    /// <summary>
    /// Throws an <see cref="ArgumentException"/> if the given string is
    /// <see langword="null"/> or empty.
    /// </summary>
    /// <param name="value">The value to validate.</param>
    /// <param name="paramName">The parameter name for the exception.</param>
    /// <returns>The validated, non-null value.</returns>
    /// <exception cref="ArgumentException">
    /// Thrown when <paramref name="value"/> is null or empty.
    /// </exception>
    public static string NotNullOrEmpty(string? value, string paramName)
    {
        if (string.IsNullOrEmpty(value))
        {
            throw new ArgumentException(
                "The value must not be null or empty.", paramName);
        }

        return value;
    }
}
