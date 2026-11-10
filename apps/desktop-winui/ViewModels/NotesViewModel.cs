using LiveCaptions.Desktop.Models;
using LiveCaptions.Desktop.Services;

namespace LiveCaptions.Desktop.ViewModels;

/// <summary>Notebook requests and selection, independent of XAML.</summary>
public sealed class NotesViewModel(IBackendClient backend)
{
    public List<NoteGroup> Groups { get; private set; } = [];
    public List<NoteEntry> Notes { get; private set; } = [];
    public NoteGroup? Group { get; private set; }
    public NoteEntry? Note { get; private set; }

    public async Task LoadAsync(string? meetingId, string? groupId = null, string? noteId = null)
    {
        if (groupId is null && !string.IsNullOrEmpty(meetingId))
            groupId = WireJson.Read<NoteGroup>(await backend.CallAsync("notes.meeting_group",
                new { meeting_id = meetingId })).Id;
        await RefreshGroupsAsync();
        await OpenGroupAsync(Groups.FirstOrDefault(g => g.Id == groupId) ?? Groups.FirstOrDefault());
        if (noteId is not null) Note = Notes.FirstOrDefault(n => n.Id == noteId);
    }

    private async Task RefreshGroupsAsync()
    {
        Groups = WireJson.Read<List<NoteGroup>>(await backend.CallAsync("notes.groups"));
        Group = Groups.FirstOrDefault(g => g.Id == Group?.Id);
    }

    public async Task OpenGroupAsync(NoteGroup? group)
    {
        var notes = group is null ? [] : WireJson.Read<List<NoteEntry>>(
            await backend.CallAsync("notes.list", new { group_id = group.Id }));
        Group = group; Notes = notes; Note = Notes.FirstOrDefault();
    }

    public void SelectNote(NoteEntry? note) => Note = note;

    public async Task SaveAsync(string title, string content)
    {
        if (Note is null || (title == Note.Title && content == Note.Content)) return;
        var saved = WireJson.Read<NoteEntry>(await backend.CallAsync("notes.update",
            new { note_id = Note.Id, title, content }));
        var index = Notes.FindIndex(n => n.Id == saved.Id);
        if (index >= 0) Notes[index] = saved;
        Note = saved;
    }

    public async Task CreateGroupAsync()
    {
        var group = WireJson.Read<NoteGroup>(await backend.CallAsync("notes.group.create",
            new { title = "新分组" }));
        await RefreshGroupsAsync();
        await OpenGroupAsync(Groups.First(g => g.Id == group.Id));
    }

    public async Task RenameGroupAsync(string title)
    {
        if (Group is null) return;
        await backend.CallAsync("notes.group.rename", new { group_id = Group.Id, title });
        await RefreshGroupsAsync();
    }

    public async Task DeleteGroupAsync()
    {
        if (Group is null) return;
        await backend.CallAsync("notes.group.delete", new { group_id = Group.Id });
        await RefreshGroupsAsync();
        await OpenGroupAsync(Groups.FirstOrDefault());
    }

    public async Task CreateNoteAsync()
    {
        if (Group is null) await CreateGroupAsync();
        var note = WireJson.Read<NoteEntry>(await backend.CallAsync("notes.create",
            new { group_id = Group!.Id, title = "新笔记", content = "" }));
        await OpenGroupAsync(Group);
        Note = Notes.First(n => n.Id == note.Id);
        await RefreshGroupsAsync();
    }

    public async Task DeleteNoteAsync()
    {
        if (Note is null) return;
        await backend.CallAsync("notes.delete", new { note_id = Note.Id });
        await OpenGroupAsync(Group);
        await RefreshGroupsAsync();
    }

    public static async Task<NoteEntry> AddAnswerAsync(IBackendClient backend,
        string meetingId, string requestId) =>
        WireJson.Read<NoteEntry>(await backend.CallAsync("notes.from_chat",
            new { meeting_id = meetingId, request_id = requestId }));

    public static async Task<List<(NoteGroup Group, List<NoteEntry> Notes)>> SidebarAsync(IBackendClient backend)
    {
        var groups = WireJson.Read<List<NoteGroup>>(await backend.CallAsync("notes.groups"));
        var result = new List<(NoteGroup, List<NoteEntry>)>();
        foreach (var group in groups)
        {
            var notes = WireJson.Read<List<NoteEntry>>(await backend.CallAsync("notes.list",
                new { group_id = group.Id }));
            result.Add((group, notes));
        }
        return result;
    }
}
