using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;

namespace LiveCaptions.Desktop.Views;

/// <summary>Shared presentation for icon actions, including screen-reader names and hover labels.</summary>
public static class IconActions
{
    public static void Describe(FrameworkElement control, string label)
    {
        AutomationProperties.SetName(control, label);
        ToolTipService.SetToolTip(control, label);
    }

    public static Button Create(string glyph, string label)
    {
        var button = new Button
        {
            Content = new FontIcon { Glyph = glyph, FontSize = 18 },
            Style = (Style)Application.Current.Resources["ToolbarIconButtonStyle"]
        };
        Describe(button, label);
        return button;
    }

    public static Grid WithField(FrameworkElement field, Button action)
    {
        var row = new Grid { ColumnSpacing = 8 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        action.VerticalAlignment = VerticalAlignment.Bottom;
        Grid.SetColumn(action, 1);
        row.Children.Add(field); row.Children.Add(action);
        return row;
    }
}
